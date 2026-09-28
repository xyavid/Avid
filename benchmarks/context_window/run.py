"""上下文阈值随窗口的 E2E：真跑 ``agent_loop``，把模型换成"按窗口记账的标尺"。

跑的是真的循环、真的 ``context.prepare````、真的五步压缩实现、真的 hook 与真的落盘；
只把**模型**换成一个不联网的标尺：它按"CJK 1 字符 ≈ 1 token、其余 4 字符 ≈ 1 token"
数出这次请求的 token 数，超过窗口就像 provider 一样抛 ``PromptTooLongError``。
这样"阈值有没有在发请求之前起作用"第一次变成可测量的东西——被测量的是**真实请求**，
不是内核自己的估算。

四个臂：

=====================  ==========  ==========  ==========================================
臂                     窗口       内容语言    它证明什么
=====================  ==========  ==========  ==========================================
``cjk_32k_fixed``       32 768     中文        写死的 400k 阈值下，③④ 一次都没赶上，
                                              是 provider 报超限之后才压的（旧行为）
``cjk_32k_window``      32 768     中文        同一份对话：阈值随窗口派生后 0 次超限，
                                              压缩发生在发请求之前
``ascii_32k_window``    32 768     ASCII       同窗口下 ASCII 的阈值是中文的 3 倍以上：
                                              语言差异被"实测 chars/token"吸收掉了
``ascii_1m_first``      1 000 000  ASCII       首轮还没有真实读数 → 逐字回落到 400k 常量
                                              （这是本次改动**有意保留**的那一段）
=====================  ==========  ==========  ==========================================

用法（不需要模型配置，也不产生任何 API 调用）：

    uv run --no-sync python benchmarks/context_window/run.py
    uv run --no-sync python benchmarks/context_window/run.py --arms cjk_32k_window

产物：``artifact.json``（每个臂的窗口、语言、阈值、超限次数、压缩步序与结论），
结论是布尔量——重跑同一条命令得到同样的结论；退出码 1 表示有结论不成立。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from avid.ai.client import PromptTooLongError, Turn, Usage  # noqa: E402
from avid.ai.config import Config  # noqa: E402
from avid.ai.transcript import text_of  # noqa: E402
from avid.policy import compaction as compact  # noqa: E402
from avid.policy.compaction import CONTEXT_CHAR_LIMIT  # noqa: E402
from avid.runtime import events as events_module  # noqa: E402
from avid.runtime.context_manager import ContextBudget  # noqa: E402
from avid.runtime.loop import agent_loop  # noqa: E402
from avid.runtime.state import RunState  # noqa: E402
from avid.tools.schemas import READ_FILE  # noqa: E402

ARTIFACT = Path(__file__).resolve().parent / "artifact.json"

#: 标尺的换算率：CJK 一字一 token、其余四字符一 token。这两个数**只属于标尺**，
#: 内核拿不到它们——它只能从真实读数里反推（这正是被测的东西）。
CJK_PER_TOKEN = 1.0
OTHER_PER_TOKEN = 4.0

SUMMARY_MARK = "压缩成要点摘要"

#: 落盘文件名里带每次运行都不同的 run_tag；产物要能重跑出同一份，所以把它抹成占位符。
_RUN_TAG = re.compile(r"((?:transcript|tool-result|tool-output)-)[0-9a-f]{4,}-\d+")


def _normalize(text: str) -> str:
    return _RUN_TAG.sub(r"\1<run_tag>-<seq>", text)


def _tokens(text: str) -> int:
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return cjk + round((len(text) - cjk) / OTHER_PER_TOKEN)


def _prompt_tokens(system: str, tools: Any, messages: list[dict[str, Any]]) -> int:
    """把"这次请求的全部文本"按标尺折算成 token——与内核记的字符数同一份输入。"""
    blobs = [system or ""]
    if tools:
        blobs.append(json.dumps(tools, ensure_ascii=False))
    for message in messages:
        blobs.append(text_of(message.get("content")))
        if message.get("tool_calls"):
            blobs.append(json.dumps(message["tool_calls"], ensure_ascii=False))
    return _tokens("".join(blobs))


@dataclass(frozen=True)
class Arm:
    name: str
    window: int
    chunk: str
    rounds: int
    #: False = 注入一个固定阈值（旧行为）；True = 用内核默认（派生生效）。
    from_window: bool = True
    question: str = "把日志读一遍，然后总结。"
    note: str = ""


class Scale:
    """按窗口记账的"模型"：不联网、不计费，只在超过窗口时像 provider 那样报超限。"""

    def __init__(self, arm: Arm, timeline: list[dict[str, Any]]) -> None:
        self.arm = arm
        self.timeline = timeline
        self.calls = 0
        self.issued = 0
        self.overflows = 0
        self.summaries = 0
        self.max_tokens = 0

    def __call__(self, config: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Turn:
        tokens = _prompt_tokens(kwargs.get("system") or "", kwargs.get("tools"), messages)
        self.calls += 1
        self.max_tokens = max(self.max_tokens, tokens)
        self.timeline.append({"event": "request", "call": self.calls, "tokens": tokens})

        if tokens > self.arm.window:
            self.overflows += 1
            self.timeline.append(
                {"event": "overflow", "call": self.calls, "tokens": tokens}
            )
            raise PromptTooLongError(
                f"标尺：这次请求 {tokens} token，超过窗口 {self.arm.window}"
            )

        if SUMMARY_MARK in str(messages[-1].get("content") or ""):
            self.summaries += 1
            return self._final("早前的读取已压成摘要。", tokens)

        if self.issued < self.arm.rounds:
            self.issued += 1
            call = {
                "id": f"call_{self.issued}",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": f"log/{self.issued:03d}.txt"}),
                },
            }
            return Turn(
                message={"role": "assistant", "content": "", "tool_calls": [call]},
                text="",
                tool_calls=[call],
                usage=Usage(tokens, 1, tokens + 1),
                model="scale",
                finish_reason="tool_calls",
            )

        return self._final("读完了，日志里没别的东西。", tokens)

    def _final(self, text: str, tokens: int) -> Turn:
        return Turn(
            message={"role": "assistant", "content": text},
            text=text,
            tool_calls=[],
            usage=Usage(tokens, 1, tokens + 1),
            model="scale",
            finish_reason="stop",
        )


def _instrument(
    limits: list[int],
) -> Callable[[], None]:
    """把 ③④ 收到的 ``limit`` 记下来（不改行为）。调用返回的函数即还原。

    读的是**编排交给压缩步骤的那个数**：它是不是派生的、派生成多少，一次都逃不掉。
    真实发生的压缩不从这里看——那走 ``context_compacted`` 事件（只有真的压了才有）。
    """
    originals = {}
    for name in ("micro_compact", "compact_history"):
        original = getattr(compact, name)
        originals[name] = original

        def wrapper(*args: Any, _original=original, **kwargs: Any) -> Any:
            if kwargs.get("limit") is not None:
                limits.append(int(kwargs["limit"]))
            return _original(*args, **kwargs)

        setattr(compact, name, wrapper)

    def restore() -> None:
        for name, original in originals.items():
            setattr(compact, name, original)

    return restore


def run_arm(arm: Arm) -> dict[str, Any]:
    timeline: list[dict[str, Any]] = []
    limits: list[int] = []
    emitted: list[Any] = []
    details: list[str] = []
    scale = Scale(arm, timeline)

    def observe(event: Any) -> None:
        emitted.append(event)
        if event.type == events_module.CONTEXT_COMPACTED:
            # 压缩事件与请求事件同线程同序：谁先谁后在 timeline 里是事实。
            timeline.append(
                {
                    "event": "compaction",
                    "step": event.data.get("step"),
                    "call": scale.calls,
                    "before": event.data.get("before"),
                    "after": event.data.get("after"),
                }
            )
            details.append(_normalize(str(event.data.get("detail") or "")))

    def read_file(arguments: dict[str, Any], *, state: Any = None) -> str:
        return arm.chunk

    budget = (
        None
        if arm.from_window
        else ContextBudget(context_chars=CONTEXT_CHAR_LIMIT, from_window=False)
    )
    config = Config(
        api_key="scale",
        base_url="http://scale.invalid/v1",
        model="scale",
        context_window=arm.window,
    )

    status, error, text = "finished", None, ""
    restore = _instrument(limits)
    try:
        with tempfile.TemporaryDirectory(prefix="avid-context-window-") as workdir:
            state = RunState.for_run(
                auto_approve=True,
                workspace_root=workdir,
                observer=observe,
            )
            try:
                text = agent_loop(
                    [{"role": "user", "content": arm.question}],
                    config=config,
                    state=state,
                    chat=scale,
                    budget=budget,
                    tools=[READ_FILE],
                    registry={"read_file": read_file},
                )
            except PromptTooLongError as exc:  # 兜底也救不回来：模型报错直接冒出来
                status, error = "prompt_too_long_escaped", str(exc)
            except Exception as exc:  # noqa: BLE001 - 仪器要记下任何形态的失败
                status, error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        restore()

    steps = [item for item in timeline if item["event"] == "compaction"]
    first_overflow = next(
        (index for index, item in enumerate(timeline) if item["event"] == "overflow"),
        None,
    )
    if first_overflow is None:
        before_first = [item["step"] for item in steps]
    else:
        before_first = [
            item["step"] for item in timeline[:first_overflow] if item["event"] == "compaction"
        ]

    return {
        "arm": arm.name,
        "note": arm.note,
        "window": arm.window,
        "chunk_chars": len(arm.chunk),
        "rounds": arm.rounds,
        "fixed_threshold": budget is not None,
        "status": status,
        "error": error,
        "final_text": text,
        "model_calls": scale.calls,
        "overflows": scale.overflows,
        "summaries": scale.summaries,
        "max_prompt_tokens": scale.max_tokens,
        "max_utilization": round(scale.max_tokens / arm.window, 3),
        "compaction_steps": [item["step"] for item in steps],
        "steps_before_first_overflow": before_first,
        "limits_handed_to_compaction": limits,
        "detail_has_derived_note": any("阈值随窗口派生" in item for item in details),
        "details": details[:3],
    }


def _conclusions(arms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    old = arms["cjk_32k_fixed"]
    new = arms["cjk_32k_window"]
    ascii_arm = arms["ascii_32k_window"]
    first = arms["ascii_1m_first"]

    cjk_limit = new["limits_handed_to_compaction"][-1] if new["limits_handed_to_compaction"] else 0
    ascii_limit = (
        ascii_arm["limits_handed_to_compaction"][-1]
        if ascii_arm["limits_handed_to_compaction"]
        else 0
    )

    return [
        {
            "id": "C1",
            "text": (
                "固定 400k 阈值（旧行为）：32k 窗口下 ③④ 一次都没赶上——撞窗之前发生的"
                "压缩步数是 0，运行只能靠 provider 报超限才发现"
            ),
            "pass": old["overflows"] >= 1
            and not old["steps_before_first_overflow"]
            and not old["limits_handed_to_compaction"],
        },
        {
            "id": "C2",
            "text": (
                "阈值随窗口派生（新行为）：同一份对话、同一个窗口，0 次请求超窗，"
                "且 ③ 真的在发请求之前压过"
            ),
            "pass": new["overflows"] == 0 and "micro_compact" in new["compaction_steps"],
        },
        {
            "id": "C3",
            "text": "派生理由进了用户可见的 detail（界面据此回答「为什么现在压」）",
            "pass": bool(new["detail_has_derived_note"]),
        },
        {
            "id": "C4",
            "text": (
                "语言差异被吸收：同一个 32k 窗口，ASCII 臂的派生阈值是中文臂的 3 倍以上"
                f"（{ascii_limit} vs {cjk_limit} 字符）"
            ),
            "pass": ascii_limit >= 3 * cjk_limit > 0,
        },
        {
            "id": "C5",
            "text": (
                "首轮（还没有真实读数）逐字回落到 400_000 常量，detail 里也没有派生说明"
            ),
            "pass": bool(first["limits_handed_to_compaction"])
            and set(first["limits_handed_to_compaction"]) == {CONTEXT_CHAR_LIMIT}
            and not first["detail_has_derived_note"],
        },
    ]


def _print(arms: list[dict[str, Any]], conclusions: list[dict[str, Any]]) -> None:
    print(f"{'臂':<18}{'窗口':>9}{'超限':>5}{'最大占用':>9}{'交给③④的阈值':>16}  压缩步序")
    for item in arms:
        steps = ",".join(item["compaction_steps"]) or "-"
        limits = ",".join(str(value) for value in item["limits_handed_to_compaction"]) or "-"
        print(
            f"{item['arm']:<18}{item['window']:>9}{item['overflows']:>5}"
            f"{item['max_utilization']:>9.2f}{limits:>16}  {steps}"
        )
    print()
    for item in conclusions:
        print(f"[{'PASS' if item['pass'] else 'FAIL'}] {item['id']} {item['text']}")
    passed = sum(1 for item in conclusions if item["pass"])
    print(f"\n{passed}/{len(conclusions)} 条结论成立")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="context_window", description=__doc__)
    parser.add_argument("--arms", help="逗号分隔的臂名；默认全部")
    args = parser.parse_args(argv)

    all_arms = _arms()
    chosen = all_arms
    if args.arms:
        wanted = {name.strip() for name in args.arms.split(",") if name.strip()}
        chosen = [arm for arm in all_arms if arm.name in wanted]
        missing = wanted - {arm.name for arm in all_arms}
        if missing:
            parser.error(f"没有这些臂：{'、'.join(sorted(missing))}")

    results = [run_arm(arm) for arm in chosen]
    by_name = {item["arm"]: item for item in results}
    conclusions = (
        _conclusions(by_name) if len(by_name) == len(all_arms) else []
    )

    ARTIFACT.write_text(
        json.dumps(
            {
                "suite": "context_window",
                "how": "uv run --no-sync python benchmarks/context_window/run.py",
                "arms": results,
                "conclusions": conclusions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _print(results, conclusions)
    print(f"产物：{ARTIFACT}")
    if conclusions and not all(item["pass"] for item in conclusions):
        return 1
    return 0


def _arms() -> list[Arm]:
    return [
        Arm(
            name="cjk_32k_fixed",
            window=32_768,
            chunk="数据" * 1_000,  # 2 000 CJK 字符 ≈ 2 000 token
            rounds=20,
            from_window=False,
            note="写死的 400k 阈值（把派生关掉，复现改动前的行为）",
        ),
        Arm(
            name="cjk_32k_window",
            window=32_768,
            chunk="数据" * 1_000,
            rounds=20,
            note="同一份对话，阈值随窗口派生",
        ),
        Arm(
            name="ascii_32k_window",
            window=32_768,
            chunk="lorem ipsum dolor sit amet " * 267,  # ≈ 7 200 ASCII 字符 ≈ 1 800 token
            rounds=20,
            note="同窗口、同 token 量级，但内容是 ASCII",
        ),
        Arm(
            name="ascii_1m_first",
            window=1_000_000,
            chunk="",
            rounds=0,
            question="lorem ipsum dolor sit amet " * 20_000,  # 50 万字符，单条 user
            note="首轮：有窗口但还没有真实读数",
        ),
    ]


if __name__ == "__main__":
    raise SystemExit(main())
