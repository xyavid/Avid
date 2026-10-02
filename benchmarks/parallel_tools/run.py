"""并行工具调用的 E2E 验收：同一条命令重跑得到同样的**结论**。

跑的是真的 `svc` 注册表 + 真的会话落盘 + 真的工具注册表 + 真的内核循环；只把两样
东西换成受控的：

* **模型**换成脚本（一次回复里固定发 5 个 tool_calls）——真模型不会每次都发同一批；
* 给工具 handler 注入一段固定延迟（默认 150 ms）——`read_file` 读几十字节是亚毫秒级
  的，"有没有重叠"在毫秒时间戳上根本看不出来。注入的是延迟，不是行为：handler 仍然
  是 `avid.tools` 里那一个，参数校验、权限、截断、事件全部走真实路径。

一条批（一次回复 5 个调用）：4 个读类 = 一个并发段 + 1 个写类 = 屏障。

    读出 a/b/c.txt（并发段） → write out.txt（屏障）

三个臂各跑一遍：`AVID_MAX_PARALLEL_TOOL_CALLS` = 10 / 2 / 1。

产物 `artifact.json` 里只有**布尔结论**与时序信息；结论不随机器快慢变化，时序会变。

用法：

    uv run --no-sync python benchmarks/parallel_tools/run.py
    uv run --no-sync python benchmarks/parallel_tools/run.py --latency-ms 300 --out /tmp/a.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from avid.ai.client import Turn, Usage  # noqa: E402
from avid.runtime import events  # noqa: E402
from avid.svc import Services  # noqa: E402
from avid.tools import TOOL_IMPLS  # noqa: E402

CALLS: list[tuple[str, str, str]] = [
    ("c1", "read_file", '{"path": "a.txt"}'),
    ("c2", "read_file", '{"path": "b.txt"}'),
    ("c3", "glob", '{"pattern": "*.txt"}'),
    ("c4", "read_file", '{"path": "c.txt"}'),
    ("c5", "write_file", '{"path": "out.txt", "content": "done"}'),
]

FINAL_TEXT = "三个文件都读完了，out.txt 也写好了。"


class ScriptedChat:
    """第一轮发一整批调用，第二轮收尾。多要一轮就是 bug，直接报错。"""

    def __init__(self) -> None:
        self.turns = [
            make_turn("", CALLS),
            make_turn(FINAL_TEXT, []),
        ]

    def __call__(self, config: Any, messages: list[dict], **kwargs: Any) -> Turn:
        index = getattr(self, "_index", 0)
        self._index = index + 1
        if index >= len(self.turns):
            raise AssertionError(f"模型被多要了一轮（脚本只给 {len(self.turns)} 轮）")
        return self.turns[index]


def make_turn(text: str, calls: list[tuple[str, str, str]]) -> Turn:
    tool_calls = [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
        for call_id, name, args in calls
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return Turn(
        message=message,
        text=text,
        tool_calls=tool_calls,
        usage=Usage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        model="scripted",
        finish_reason="tool_calls" if tool_calls else "stop",
    )


class SlowTools:
    """真 handler + 固定延迟；顺手记录每次调用的进入/退出（给时序做交叉验证）。"""

    def __init__(self, latency_s: float) -> None:
        self.latency_s = latency_s
        self._lock = threading.Lock()
        self.spans: list[tuple[str, float, float]] = []

    def registry(self, names: tuple[str, ...]) -> dict[str, Any]:
        return {name: self._wrap(name) for name in names}

    def _wrap(self, name: str):
        handler = TOOL_IMPLS[name]

        def run(arguments, **kwargs):
            start = time.monotonic()
            try:
                time.sleep(self.latency_s)
                return handler(arguments, **kwargs)
            finally:
                end = time.monotonic()
                with self._lock:
                    self.spans.append((name, start, end))

        return run


def wait_for(predicate, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def run_arm(root: Path, max_parallel: int, latency_ms: int) -> dict[str, Any]:
    """跑一个臂，返回这个臂的全部可观测事实。"""
    tools = SlowTools(latency_ms / 1000)
    services = Services(
        workspace_root=root,
        chat=ScriptedChat(),
        tool_registry=tools.registry(("read_file", "glob", "write_file")),
    )
    session_id = services.sessions.create(
        workspace=services.workspaces.default.id, name=f"parallel-{max_parallel}"
    )["id"]

    os.environ["AVID_MAX_PARALLEL_TOOL_CALLS"] = str(max_parallel)
    started = time.monotonic()
    record = services.runs.start(session_id, "读三个文件并写一个结果文件", auto_approve=True)
    assert wait_for(lambda: record.terminal), f"运行没结束：{record.status}"
    wall_ms = int((time.monotonic() - started) * 1000)

    frames = [event for event in services.runs.subscribe(record.run_id) if event is not None]
    entry_page = services.sessions.entries(session_id, order="asc")
    tool_messages = [
        {
            "order": index,
            "tool_call_id": str(entry["message"].get("tool_call_id", "")),
            "content": str(entry["message"].get("content", "")),
        }
        for index, entry in enumerate(entry_page["entries"])
        if entry["type"] == "message" and entry["message"].get("role") == "tool"
    ]

    return {
        "max_parallel": max_parallel,
        "status": record.status,
        "final_text": record.text,
        "wall_clock_ms": wall_ms,
        "tool_events": [
            {
                "type": event.type,
                "tool": str(event.data.get("tool", "")),
                "tool_call_id": str(event.data.get("tool_call_id", "")),
                "ts": event.ts,
                "parallel": event.data.get("parallel"),
                "duration_ms": event.data.get("duration_ms"),
            }
            for event in frames
            if event.type in (events.TOOL_CALL_STARTED, events.TOOL_CALL_FINISHED)
        ],
        "tool_messages": tool_messages,
        "spans": [
            {"tool": name, "start": round(start, 4), "end": round(end, 4)}
            for name, start, end in tools.spans
        ],
    }


def windows(arm: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """每个 ``tool_call_id`` 的 [started, finished] 时间窗（取内核事件里的 ts）。"""
    started: dict[str, int] = {}
    finished: dict[str, int] = {}
    for event in arm["tool_events"]:
        call_id = event["tool_call_id"]
        if event["type"] == events.TOOL_CALL_STARTED:
            started[call_id] = event["ts"]
        elif event["type"] == events.TOOL_CALL_FINISHED:
            finished[call_id] = event["ts"]
    return {
        call_id: (begin, finished.get(call_id, begin))
        for call_id, begin in started.items()
    }


def overlapping_pairs(arm: dict[str, Any]) -> list[list[str]]:
    spans = windows(arm)
    found: list[list[str]] = []
    for left, right in combinations(sorted(spans), 2):
        (a_start, a_end), (b_start, b_end) = spans[left], spans[right]
        if a_start < b_end and b_start < a_end:
            found.append([left, right])
    return found


def max_in_flight(arm: dict[str, Any]) -> int:
    """任一时刻同时在跑的调用数峰值。

    时间戳是**毫秒**解析度：串行时前一个的结束与后一个的开始常常同毫秒，所以同一时刻
    必须先处理"结束"（-1）再处理"开始"（+1），否则 T 型相接会被误判成重叠。
    """
    stamps: list[tuple[int, int]] = []
    for start, end in windows(arm).values():
        stamps.append((start, 1))
        stamps.append((end, -1))
    peak = 0
    live = 0
    for _, delta in sorted(stamps, key=lambda item: (item[0], item[1])):
        live += delta
        peak = max(peak, live)
    return peak


def _seed_scripted_byok() -> None:
    """模型是脚本：种一份最小 BYOK 配置，让 resolve_chat() 有东西可解析。

    base_url 指到 .invalid——脚本 chat 根本不发请求；配置存在的意义是让
    「没有模型配置」这条错误路径闭嘴。
    """
    byok_dir = Path(tempfile.mkdtemp(prefix="avid-e2e-byok-"))
    (byok_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "scripted",
                        "label": "Scripted",
                        "protocol": "openai-compatible",
                        "base_url": "https://scripted.invalid/v1",
                        "auth": {"type": "bearer", "secret_ref": "scripted"},
                        "models": [{"id": "scripted"}],
                    }
                ],
                "bindings": {"chat": "scripted/scripted"},
            }
        ),
        encoding="utf-8",
    )
    (byok_dir / "secrets.json").write_text(
        json.dumps({"scripted": "e2e-not-a-real-key"}), encoding="utf-8"
    )
    os.environ["AVID_BYOK_CONFIG"] = str(byok_dir / "models.json")
    os.environ["AVID_BYOK_SECRETS"] = str(byok_dir / "secrets.json")
    os.environ["AVID_MODEL_INFO"] = "off"  # 不打真实端点


def main() -> int:
    parser = argparse.ArgumentParser(description="并行工具调用 E2E（产出可重复的 JSON）")
    parser.add_argument("--latency-ms", type=int, default=150, help="注入到每个工具调用的延迟")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).with_name("artifact.json")),
        help="产物路径（默认写在脚本旁边）",
    )
    parser.add_argument("--arms", default="10,2,1", help="逗号分隔的并发上限")
    args = parser.parse_args()

    _seed_scripted_byok()

    arms: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="avid-parallel-e2e-") as tmp:
        root = Path(tmp)
        for name, content in (("a.txt", "alpha\n"), ("b.txt", "beta\n"), ("c.txt", "gamma\n")):
            (root / name).write_text(content, encoding="utf-8")
        for limit in (int(item) for item in args.arms.split(",") if item.strip()):
            # 每个臂一个干净工作区（out.txt / 会话文件不互相污染）
            arm_root = root / f"arm-{limit}"
            arm_root.mkdir()
            for name, content in (
                ("a.txt", "alpha\n"),
                ("b.txt", "beta\n"),
                ("c.txt", "gamma\n"),
            ):
                (arm_root / name).write_text(content, encoding="utf-8")
            # AVID_HOME 也隔离开，免得碰真实工作区注册表
            os.environ["AVID_HOME"] = str(arm_root / ".avid-home")
            arms[f"parallel_{limit}"] = run_arm(arm_root, limit, args.latency_ms)

    wide = arms[f"parallel_{(max(int(i) for i in args.arms.split(',')))}"]
    serial = arms["parallel_1"]
    narrow = arms.get("parallel_2")

    wide_peak = max_in_flight(wide)
    serial_peak = max_in_flight(serial)
    wide_overlaps = overlapping_pairs(wide)
    serial_overlaps = overlapping_pairs(serial)

    def tool_messages(arm: dict[str, Any]) -> list[tuple[str, str]]:
        return [(item["tool_call_id"], item["content"]) for item in arm["tool_messages"]]

    write_window = windows(wide).get("c5")
    write_ok = write_window is not None and all(
        not (start < write_window[1] and write_window[0] < end)
        for call_id, (start, end) in windows(wide).items()
        if call_id != "c5"
    )

    checks = {
        "同一个批在串行与并行下 transcript 逐条相同（含顺序）": tool_messages(wide)
        == tool_messages(serial),
        "最终回答相同": wide["final_text"] == serial["final_text"],
        "两条路径都跑成了 finished": wide["status"] == serial["status"] == "finished",
        "并行档确实发生了重叠": len(wide_overlaps) > 0,
        "串行档一次都没有重叠": serial_overlaps == [],
        "写调用（屏障）不与任何读重叠": bool(write_ok),
        "并发峰值不超过上限（10）": wide_peak <= 10,
        "并发峰值用满了并发段（4 个读）": wide_peak == 4,
        "串行档峰值恒为 1": serial_peak == 1,
        "started 事件带 parallel=并发段宽度": all(
            event["parallel"] in (1, 4) for event in wide["tool_events"]
        ),
    }
    if narrow is not None:
        checks["上限=2 时峰值被压到 2"] = max_in_flight(narrow) == 2

    artifact = {
        "what": "并行工具调用 E2E：同一条命令重跑得到同样的结论",
        "note": (
            "模型是脚本（固定一轮 5 个调用），工具 handler 是真实现 + 注入固定延迟；"
            "svc 注册表、会话落盘、事件流、权限与截断都走真实路径。"
            "checks 是布尔结论，不随机器快慢变化；wall_clock_ms 只是参考。"
        ),
        "injected_latency_ms": args.latency_ms,
        "batch": [{"tool_call_id": cid, "tool": name, "arguments": raw} for cid, name, raw in CALLS],
        "arms": arms,
        "observed": {
            "max_in_flight": {
                name: max_in_flight(arm) for name, arm in arms.items()
            },
            "overlapping_pairs": {
                name: overlapping_pairs(arm) for name, arm in arms.items()
            },
            "wall_clock_ms": {name: arm["wall_clock_ms"] for name, arm in arms.items()},
        },
        "checks": checks,
        "all_passed": all(checks.values()),
    }

    out = Path(args.out)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"产物：{out}")
    for name, arm in arms.items():
        print(
            f"  {name:<12} 峰值并发={max_in_flight(arm)}  "
            f"墙钟={arm['wall_clock_ms']}ms  重叠对={len(overlapping_pairs(arm))}"
        )
    for label, passed in checks.items():
        print(f"  [{'OK' if passed else 'FAIL'}] {label}")
    print("结论：" + ("全部通过" if artifact["all_passed"] else "有未通过的检查"))
    return 0 if artifact["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
