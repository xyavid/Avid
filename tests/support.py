"""svc / web 测试共用的脚本化模型与等待工具。

放在 ``tests/`` 根下的普通模块（pytest 会把测试目录放进 ``sys.path``），这样
每个测试文件都能 ``from support import ...``，不必各自复制一份 FakeChat。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable

from avid.ai.client import Turn, Usage


def make_turn(text: str = "", tool_calls: Iterable[dict] = (), finish_reason: str = "stop") -> Turn:
    calls = list(tool_calls)
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = calls
    return Turn(
        message=message,
        text=text,
        tool_calls=calls,
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="m",
        finish_reason=finish_reason,
    )


def tool_call(name: str, arguments: str = "{}", call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class ScriptedChat:
    """按顺序返回预设轮次。多要一轮就报错——测试里那是 bug，不该静默。"""

    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, config: Any, messages: list[dict], **kwargs: Any) -> Turn:
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        index = len(self.requests) - 1
        if index >= len(self.turns):
            raise AssertionError(f"模型被多要了一轮（已给 {len(self.turns)} 轮）")
        return self.turns[index]


class RecordingTools:
    """最小工具注册表：记录调用，不真的碰磁盘或 shell。"""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.results = results or {}

    def registry(self, *names: str) -> dict[str, Callable[[dict], str]]:
        return {name: self._handler(name) for name in names}

    def _handler(self, name: str) -> Callable[[dict], str]:
        def run(arguments: dict) -> str:
            self.calls.append((name, arguments))
            return self.results.get(name, f"{name} ok")

        return run


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def wait_status(record: Any, status: str, timeout: float = 5.0) -> bool:
    return wait_for(lambda: record.status == status, timeout)


def wait_terminal(record: Any, timeout: float = 5.0) -> bool:
    return wait_for(lambda: record.terminal, timeout)


def collect(services: Any, run_id: str, after: int = 0, deltas: bool = False) -> list:
    """把订阅到的帧收完（跳过心跳）。运行结束时生成器会自然结束。"""
    return [
        event
        for event in services.runs.subscribe(run_id, after=after, deltas=deltas)
        if event is not None
    ]


def new_session(services: Any, name: str | None = None) -> str:
    return services.sessions.create(name=name)["id"]
