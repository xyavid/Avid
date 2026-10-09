"""Shared scripted model and wait helpers for the svc and web test families.

A plain module in ``tests/`` (pytest puts the test directory on ``sys.path``) so every test file
can ``from support import ...``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from avid.providers.client import Turn, Usage


def make_turn(
    text: str = "",
    tool_calls: Iterable[dict] = (),
    finish_reason: str = "stop",
    reasoning: str = "",
) -> Turn:
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
        reasoning=reasoning,
    )


# Load-bearing constraint: placeholder args must pass schema validation (validation sits past
# execute_one, see tools/validate.py), kept in sync with the registry by test_tools_contract.py.
PLACEHOLDER_ARGS: dict[str, str] = {
    "read_file": '{"path": "a.txt"}',
    "write_file": '{"path": "a.txt", "content": "x"}',
    "edit_file": '{"path": "a.txt", "old_string": "a", "new_string": "b"}',
    "glob": '{"pattern": "*.py"}',
    "grep_search": '{"pattern": "placeholder"}',
    "bash": '{"command": "echo hi"}',
    "todo_write": '{"todos": []}',
    "subagent": '{"tasks": [{"description": "子任务", "objective": "把这件事做完", '
    '"scope": "avid/agent", "context": "无", "constraints": "不要改文件", '
    '"deliverable": "结论摘要"}]}',
    "load_skill": '{"name": "demo"}',
    "ask_user": '{"question": "选哪个？", "options": ["甲", "乙"]}',
}


def tool_call(name: str, arguments: str | None = None, call_id: str = "call_1") -> dict:
    """Build one tool call; without arguments, use the schema-valid placeholder."""
    if arguments is None:
        arguments = PLACEHOLDER_ARGS.get(name, "{}")
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class ScriptedChat:
    """Return preset turns in order; asking for an extra turn raises (a test bug)."""

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
    """Minimal tool registry: records calls, never touches disk or shell."""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.results = results or {}

    def registry(self, *names: str) -> dict[str, Callable[[dict], str]]:
        return {name: self._handler(name) for name in names}

    def _handler(self, name: str) -> Callable[[dict], str]:
        def run(arguments: dict, **kwargs) -> str:
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


#: Terminal statuses that mean instrumentation/config trouble, not model capability. Real-model
#: evals must count `llm_error` here, or one 401 turns every run red while the tests stay green.
INFRA_STATUSES = ("error", "llm_error")


def real_config_or_skip():
    """Model config for real-model evals.

    conftest's autouse ``model_env`` seeds a fake test-key/test-model config for every test,
    which would 401 every eval run; a missing config skips, the fixture's fake config fails.
    """
    from avid.providers.byok import resolve_chat
    from avid.providers.config import ConfigError

    try:
        config = resolve_chat()
    except ConfigError as exc:
        pytest.skip(f"没有模型配置，跳过真模型评测：{exc}")
    if config.api_key == "test-key" or config.model == "test-model":
        pytest.fail(
            "真模型评测拿到的是测试夹具的假配置（test-key / test-model）："
            "conftest 的 model_env 对 eval / eval_smoke 标记应当让开"
        )
    return config


def assert_no_infrastructure_failures(run_set: Any) -> str:
    """Assert no instrument/config-level failures; returns the report text for printing."""
    summary = run_set.summary()
    broken = [item for item in run_set.results if item.status in INFRA_STATUSES]
    assert not broken, summary + "\n仪器或配置出错：\n" + "\n".join(
        f"{item.case_id}/{item.arm}: {item.status} {item.error}" for item in broken
    )
    return summary


def wait_status(record: Any, status: str, timeout: float = 5.0) -> bool:
    return wait_for(lambda: record.status == status, timeout)


def wait_terminal(record: Any, timeout: float = 5.0) -> bool:
    return wait_for(lambda: record.terminal, timeout)


def wait_handle_released(services: Any, session_id: str, timeout: float = 5.0) -> None:
    """Wait for the run thread to release the session handle.

    ``record.terminal`` only means the registry settled; the handle is released in the
    ``finally`` right after, so tests that ``repo.open`` the file directly must wait or hit
    ``SessionAlreadyOpenError`` (the server read path does not: it uses a handle fallback).
    """
    assert wait_for(
        lambda: services.runs.active_run_id(session_id) is None, timeout
    ), f"运行没有交还会话句柄：{session_id}"


def collect(services: Any, run_id: str, after: int = 0, deltas: bool = False) -> list:
    """Drain subscribed frames (heartbeats skipped); the generator ends when the run does."""
    return [
        event
        for event in services.runs.subscribe(run_id, after=after, deltas=deltas)
        if event is not None
    ]


def create_session(client: Any, **fields: Any):
    """Create a session over HTTP, always with a workspace.

    Sessions must name their ownership, so tests first ask the server which workspace it is
    bound to (``GET /api/workspaces``, the entry with ``is_default=true``) and pass that back.
    """
    listed = client.get("/api/workspaces").json()["workspaces"]
    assert listed, "服务端必须至少绑定一个工作地点"
    workspace = next((ws for ws in listed if ws.get("is_default")), listed[0])
    return client.post("/api/sessions", json={"workspace": workspace["id"], **fields})


def bound_workspace(services: Any) -> str:
    """The workspace id this process is bound to."""
    return services.workspaces.default.id


def new_session(services: Any, name: str | None = None) -> str:
    return services.sessions.create(workspace=bound_workspace(services), name=name)["id"]


def run_loop(messages, *, on_message=None, ask=None, on_event=None, state=None, **spec_kwargs):
    """Test entry point with the old ``agent_loop`` kwargs, backed by ``RunSpec`` + ``Run``.

    Returns the final text; callers needing the stop reason use ``Run`` directly (``RunOutcome``).
    """
    from avid.agent.run import Run
    from avid.agent.spec import RunSpec

    outcome = Run(
        messages,
        RunSpec.resolve(**spec_kwargs),
        state=state,
        on_message=on_message,
        ask=ask,
        on_event=on_event,
    ).run()
    return outcome.text

