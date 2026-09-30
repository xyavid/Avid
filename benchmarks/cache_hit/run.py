"""前缀缓存命中率的 E2E：把「system 首轮冻结」的设计意图变成数字。

两个臂：

================  ==========  ====================================================
臂                模型        它证明什么
================  ==========  ====================================================
``frozen_prefix``  离线标尺    一次运行内发给模型的 system prompt 逐字节稳定、
                              tail 每轮变化——这是服务端前缀缓存能命中的前提
``live_cache``     真模型      从第 2 轮起 provider 上报 cache_read_tokens>0，
                              且命中率（cache_read/prompt）不低于阈值
================  ==========  ====================================================

用法::

    # 离线臂：不需要模型配置，也不产生任何 API 调用
    uv run --no-sync python benchmarks/cache_hit/run.py

    # 真模型臂：读 .env 配置，按轮次计费（4 轮左右的微小对话）
    uv run --env-file .env python benchmarks/cache_hit/run.py --live

产物：``artifact.json``（每轮的 system 哈希、prompt/cache_read token 与比率、结论），
结论是布尔量——重跑同一条命令得到同样的结论；退出码 1 表示有结论不成立。

live 臂的两个前提（不满足时对应结论按不成立处理，产物里会写明原因）：

- provider 必须在 usage 里上报缓存命中（``usage.py`` 归一出的
  ``cache_read_tokens``）；不上报的网关只会得到 "未上报" 的失败。
- 对话要真的走多轮（模型至少发起一次工具调用）；单轮对话没有"后续轮"可言。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from avid.ai.client import Turn, Usage, chat_completion  # noqa: E402
from avid.ai.config import Config, load_config  # noqa: E402
from avid.ai.transcript import text_of  # noqa: E402
from avid.runtime.loop import agent_loop  # noqa: E402
from avid.runtime.state import RunState  # noqa: E402
from avid.tools.registry import specs  # noqa: E402

#: 只读小工具就够：read_file 的 schema 从注册表取（单点声明，schemas 只是封装器）。
READ_FILE = next(
    item.schema() for item in specs() if item.schema()["function"]["name"] == "read_file"
)

ARTIFACT = Path(__file__).resolve().parent / "artifact.json"

#: live 臂的工作区：三个小文件，问题要求逐个读完后汇总，保证至少几次工具轮。
LIVE_FILES = {
    "a.txt": "第一行：alpha\n第二行：略\n",
    "b.txt": "第一行：bravo\n第二行：略\n",
    "c.txt": "第一行：charlie\n第二行：略\n",
}
LIVE_QUESTION = (
    "严格按顺序来：一次只调用一个 read_file，先读 a.txt，等结果回来再读 b.txt，"
    "再读完 c.txt，最后把三个文件的第一行拼成一句话告诉我。不要并行调用。"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class CallRecord:
    """一次模型调用的观测：发出去什么、provider 报回来多少缓存。"""

    system_sha: str
    system_chars: int
    tail_sha: str
    messages_chars: int
    prompt_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass
class Recorder:
    """包在 chat 外面记录每次调用的观测；不改变任何请求内容。"""

    records: list[CallRecord] = field(default_factory=list)

    def note_request(self, system: str, messages: list[dict[str, Any]]) -> None:
        tail = messages[-1]["content"] if messages else ""
        self.records.append(
            CallRecord(
                system_sha=_sha(system),
                system_chars=len(system or ""),
                tail_sha=_sha(str(tail)),
                messages_chars=sum(len(text_of(m.get("content"))) for m in messages),
            )
        )

    def note_usage(self, usage: Usage | None) -> None:
        if self.records and usage is not None:
            last = self.records[-1]
            last.prompt_tokens = usage.prompt_tokens
            last.cache_read_tokens = usage.cache_read_tokens
            last.cache_write_tokens = usage.cache_write_tokens


class Scale:
    """离线标尺模型：固定发起 N 次 read_file 再收尾，绝不联网。"""

    def __init__(self, rounds: int, recorder: Recorder) -> None:
        self.rounds = rounds
        self.recorder = recorder
        self.issued = 0

    def __call__(self, config: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Turn:
        self.recorder.note_request(str(kwargs.get("system") or ""), messages)
        tokens = 100 + self.issued
        if self.issued < self.rounds:
            self.issued += 1
            call = {
                "id": f"call_{self.issued}",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": f"{self.issued}.txt"}),
                },
            }
            usage = Usage(tokens, 1, tokens + 1)
            self.recorder.note_usage(usage)
            return Turn(
                message={"role": "assistant", "content": "", "tool_calls": [call]},
                text="",
                tool_calls=[call],
                usage=usage,
                model="scale",
                finish_reason="tool_calls",
            )
        usage = Usage(tokens, 1, tokens + 1)
        self.recorder.note_usage(usage)
        return Turn(
            message={"role": "assistant", "content": "读完。"},
            text="读完。",
            tool_calls=[],
            usage=usage,
            model="scale",
            finish_reason="stop",
        )


def run_frozen_prefix(rounds: int) -> dict[str, Any]:
    recorder = Recorder()
    config = Config(
        api_key="scale",
        base_url="http://scale.invalid/v1",
        model="scale",
        context_window=32_768,
    )

    with tempfile.TemporaryDirectory(prefix="avid-cache-hit-") as workdir:
        state = RunState.for_run(auto_approve=True, workspace_root=workdir)

        def read_file(arguments: dict[str, Any], *, state: Any = None) -> str:
            return "内容"

        agent_loop(
            [{"role": "user", "content": "把三个文件都读一遍。"}],
            config=config,
            state=state,
            chat=Scale(rounds, recorder),
            tools=[READ_FILE],
            registry={"read_file": read_file},
        )

    systems = {item.system_sha for item in recorder.records}
    tails = {item.tail_sha for item in recorder.records}
    return {
        "arm": "frozen_prefix",
        "model": "scale(offline)",
        "calls": len(recorder.records),
        "distinct_system_hashes": len(systems),
        "distinct_tail_hashes": len(tails),
        "system_chars": recorder.records[0].system_chars if recorder.records else 0,
        "status": "finished" if recorder.records else "no_calls",
    }


def run_live(min_ratio: float) -> dict[str, Any]:
    recorder = Recorder()
    config = load_config()

    with tempfile.TemporaryDirectory(prefix="avid-cache-hit-live-") as workdir:
        for name, content in LIVE_FILES.items():
            (Path(workdir) / name).write_text(content, encoding="utf-8")
        state = RunState.for_run(auto_approve=True, workspace_root=workdir)

        def read_file(arguments: dict[str, Any], *, state: Any = None) -> str:
            path = Path(workdir) / str(arguments.get("path") or "")
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return f"错误：读不到 {arguments.get('path')}（{exc}）"

        def live_chat(conf: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Turn:
            recorder.note_request(str(kwargs.get("system") or ""), messages)
            turn = chat_completion(conf, messages, **kwargs)
            recorder.note_usage(turn.usage)
            return turn

        error: str | None = None
        try:
            agent_loop(
                [{"role": "user", "content": LIVE_QUESTION}],
                config=config,
                state=state,
                chat=live_chat,
                tools=[READ_FILE],
                registry={"read_file": read_file},
            )
        except Exception as exc:  # noqa: BLE001 - 仪器要记下任何形态的失败
            error = f"{type(exc).__name__}: {exc}"

    rounds = [
        {
            "call": index + 1,
            "system_sha": item.system_sha,
            "prompt_tokens": item.prompt_tokens,
            "cache_read_tokens": item.cache_read_tokens,
            "cache_write_tokens": item.cache_write_tokens,
            "hit_ratio": (
                round(item.cache_read_tokens / item.prompt_tokens, 3)
                if item.prompt_tokens and item.cache_read_tokens is not None
                else None
            ),
        }
        for index, item in enumerate(recorder.records)
    ]
    reported = [item for item in rounds[1:] if item["cache_read_tokens"] is not None]
    ratios = [item["hit_ratio"] for item in reported if item["hit_ratio"] is not None]
    provider_reports = any(item["cache_read_tokens"] is not None for item in rounds)
    median_ratio = round(statistics.median(ratios), 3) if ratios else None
    return {
        "arm": "live_cache",
        "model": config.model,
        "provider": config.resolved_provider,
        "status": "error" if error else "finished",
        "error": error,
        "min_ratio": min_ratio,
        "provider_reports_cache": provider_reports,
        "calls": len(rounds),
        "rounds": rounds,
        "distinct_system_hashes_live": len({item["system_sha"] for item in rounds}),
        "median_hit_ratio_from_round2": median_ratio,
    }


def _conclusions(arms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    conclusions: list[dict[str, Any]] = []
    frozen = arms.get("frozen_prefix")
    if frozen is not None:
        conclusions.append(
            {
                "id": "C1",
                "text": (
                    "一次运行内 system prompt 逐字节稳定"
                    f"（{frozen['calls']} 次调用共用 {frozen['distinct_system_hashes']} 个哈希），"
                    "而 tail 每轮变化——前缀缓存能命中的前提成立"
                ),
                "pass": frozen["calls"] >= 2
                and frozen["distinct_system_hashes"] == 1
                and frozen["distinct_tail_hashes"] == frozen["calls"],
            }
        )
    live = arms.get("live_cache")
    if live is not None:
        rounds = live["rounds"]
        hits = [(item["call"], item["cache_read_tokens"] or 0) for item in rounds[1:]]
        first_hit = next((call for call, read in hits if read > 0), None)
        # 命中一旦开始就应持续：首次命中之后的每一轮都命中。首轮之前的零是网关
        # 缓存生效的延迟（轮次表里如实呈现），不算机制失败。
        steady = first_hit is not None and all(
            read > 0 for call, read in hits if call >= first_hit
        )
        conclusions.append(
            {
                "id": "C2",
                "text": (
                    "live：provider 上报缓存命中，且命中开始后每轮持续"
                    + (f"（第 {first_hit} 轮起）" if first_hit else "")
                    if live["provider_reports_cache"]
                    else "live：provider 未上报 cache tokens，结论无法建立（换支持的网关再测）"
                ),
                "pass": (
                    live["status"] == "finished"
                    and live["provider_reports_cache"]
                    and steady
                ),
            }
        )
        median = live["median_hit_ratio_from_round2"]
        conclusions.append(
            {
                "id": "C3",
                "text": (
                    f"live：第 2 轮起命中率中位数 {median} ≥ 阈值 {live['min_ratio']}"
                    if median is not None
                    else "live：没有可计算的命中率（缺上报或缺多轮）"
                ),
                "pass": median is not None and median >= live["min_ratio"],
            }
        )
    return conclusions


def _print(arms: list[dict[str, Any]], conclusions: list[dict[str, Any]]) -> None:
    for item in arms:
        print(f"== {item['arm']}（{item['model']}）==")
        if item["arm"] == "live_cache":
            for rnd in item["rounds"]:
                print(
                    f"  轮 {rnd['call']}: prompt={rnd['prompt_tokens']} "
                    f"cache_read={rnd['cache_read_tokens']} "
                    f"cache_write={rnd['cache_write_tokens']} 比率={rnd['hit_ratio']}"
                )
            print(f"  第 2 轮起命中率中位数：{item['median_hit_ratio_from_round2']}")
        else:
            print(
                f"  调用 {item['calls']} 次，system 哈希 "
                f"{item['distinct_system_hashes']} 种，tail 哈希 {item['distinct_tail_hashes']} 种"
            )
    print()
    for item in conclusions:
        print(f"[{'PASS' if item['pass'] else 'FAIL'}] {item['id']} {item['text']}")
    passed = sum(1 for item in conclusions if item["pass"])
    print(f"\n{passed}/{len(conclusions)} 条结论成立")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cache_hit", description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="追加真模型臂（读 .env 配置，有 API 成本）"
    )
    parser.add_argument(
        "--min-ratio", type=float, default=0.5, help="live 臂命中率中位数的阈值（默认 0.5）"
    )
    parser.add_argument("--rounds", type=int, default=4, help="离线臂的工具调用轮数")
    args = parser.parse_args(argv)

    results = [run_frozen_prefix(args.rounds)]
    if args.live:
        results.append(run_live(args.min_ratio))

    by_name = {item["arm"]: item for item in results}
    conclusions = _conclusions(by_name)

    ARTIFACT.write_text(
        json.dumps(
            {
                "suite": "cache_hit",
                "how": "uv run --env-file .env python benchmarks/cache_hit/run.py --live",
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


if __name__ == "__main__":
    raise SystemExit(main())
