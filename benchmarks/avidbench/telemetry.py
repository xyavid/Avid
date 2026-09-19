"""`on_event` / `on_message` → 原始指标与轨迹。

指标**全部从事件流派生**，不从 `RunState` 字段读——这样 bare 与 avid 两个循环
用同一把尺子量。两边的 `tool_calls` / `denials` / `compactions` 与 `RunState`
的计数一致，这条由 `tests/test_bench_runner.py` 钉住（不一致就是漏了一条事件）。

轨迹是研究材料：`resolved` 只告诉你成没成，轨迹才告诉你**为什么**。落盘时
长内容会被截断（`TRAJECTORY_CHARS`），全文在 `RunResult.answer` 与工具的落盘文件里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from avid.runtime import events

from .graders import is_tool_failure

#: 轨迹里单条内容的上限。跑到 `runs/` 的是本地文件，但一次 bash 可以吐出几十万字符。
TRAJECTORY_CHARS = 8000


def clip(value: Any, limit: int = TRAJECTORY_CHARS) -> Any:
    """超长字符串截断并标注；其余类型原样返回。"""
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}…（截断，原文 {len(value)} 字符）"
    return value


@dataclass
class Telemetry:
    """一次运行的观察者。`on_event` 与 `on_message` 都直接挂在既有注入点上。"""

    trajectory: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    tokens: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    denials: int = 0
    approvals_requested: int = 0
    approvals_resolved: int = 0
    compactions: int = 0
    todo_reminders: int = 0
    stop_nudges: int = 0
    messages: int = 0
    finish_reasons: list[str] = field(default_factory=list)

    # ---------------- 两个观察点 ----------------

    def on_event(self, event: Any) -> None:
        data = dict(getattr(event, "data", None) or {})
        kind = str(getattr(event, "type", ""))
        if kind == events.RUN_STATUS:
            self.rounds = max(self.rounds, int(data.get("round") or 0))
            self.tokens = int(data.get("tokens") or self.tokens)
            reason = data.get("finish_reason")
            if reason:
                self.finish_reasons.append(str(reason))
        elif kind == events.TOOL_CALL_STARTED:
            self.tool_calls += 1
            self._note(
                "tool_call",
                tool=data.get("tool"),
                round=data.get("round"),
                arguments=data.get("arguments"),
            )
        elif kind == events.TOOL_CALL_FINISHED:
            content = str(data.get("content") or "")
            failed = is_tool_failure(content)
            if failed:
                self.tool_failures += 1
            self._note(
                "tool_result",
                tool=data.get("tool"),
                round=data.get("round"),
                failed=failed,
                duration_ms=data.get("duration_ms"),
                content=content,
            )
        elif kind == events.TOOL_CALL_DENIED:
            self.denials += 1
            self._note("tool_denied", tool=data.get("tool"), kind=data.get("kind"),
                       reason=data.get("reason"))
        elif kind == events.APPROVAL_REQUESTED:
            self.approvals_requested += 1
            self._note("approval_requested", tool=data.get("tool"), action=data.get("action"))
        elif kind == events.APPROVAL_RESOLVED:
            self.approvals_resolved += 1
            self._note("approval_resolved", tool=data.get("tool"), approved=data.get("approved"))
        elif kind == events.CONTEXT_COMPACTED:
            self.compactions += 1
            self._note("compacted", step=data.get("step"), detail=data.get("detail"),
                       before=data.get("before"), after=data.get("after"))
        elif kind == events.TODO_REMINDER:
            self.todo_reminders += 1
            self._note("todo_reminder", content=data.get("content"))
        elif kind == events.STOP_NUDGE:
            self.stop_nudges += 1
            self._note("stop_nudge", content=data.get("content"))

    def on_message(self, message: dict[str, Any]) -> None:
        self.messages += 1
        calls = [
            str(call.get("function", {}).get("name") or call.get("name") or "?")
            for call in (message.get("tool_calls") or [])
        ]
        self._note(
            "message",
            role=message.get("role"),
            tool_calls=calls,
            content=message.get("content"),
        )

    # ---------------- 轨迹 ----------------

    def _note(self, kind: str, **fields: Any) -> None:
        entry: dict[str, Any] = {"i": len(self.trajectory) + 1, "kind": kind}
        for key, value in fields.items():
            entry[key] = clip(value)
        self.trajectory.append(entry)

    def metrics(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "tokens": self.tokens,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "denials": self.denials,
            "approvals_requested": self.approvals_requested,
            "approvals_resolved": self.approvals_resolved,
            "compactions": self.compactions,
            "todo_reminders": self.todo_reminders,
            "stop_nudges": self.stop_nudges,
            "messages": self.messages,
            "finish_reasons": list(self.finish_reasons),
        }
