"""svc / web 测试共用的脚本化模型与等待工具。

放在 ``tests/`` 根下的普通模块（pytest 会把测试目录放进 ``sys.path``），这样
每个测试文件都能 ``from support import ...``，不必各自复制一份 FakeChat。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

import pytest

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


# 占位参数必须**能过 schema 校验**：参数校验收口到 `execute_one` 之后（见
# `tools/validate.py`），"{}" 这类占位会在工具执行前就被拒，测试看到的就不是它想验证
# 的那条路径了。这张表由 `test_tools_contract.py::test_placeholder_args_stay_schema_valid`
# 钉住与注册表同步。
PLACEHOLDER_ARGS: dict[str, str] = {
    "read_file": '{"path": "a.txt"}',
    "write_file": '{"path": "a.txt", "content": "x"}',
    "edit_file": '{"path": "a.txt", "old_string": "a", "new_string": "b"}',
    "glob": '{"pattern": "*.py"}',
    "bash": '{"command": "echo hi"}',
    "todo_write": '{"todos": []}',
    "subagent": '{"tasks": [{"description": "子任务", "prompt": "把这件事做完"}]}',
    "load_skill": '{"name": "demo"}',
    "web_search": '{"query": "avid agent runtime"}',
}


def tool_call(name: str, arguments: str | None = None, call_id: str = "call_1") -> dict:
    """构造一次工具调用；不传参数时按工具名取一份 schema 合法的占位参数。"""
    if arguments is None:
        arguments = PLACEHOLDER_ARGS.get(name, "{}")
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


#: 运行没走完的状态里，属于**仪器或配置**问题的那几个（不是「模型能力不够」）。
#: 真模型评测必须把 `llm_error` 也算进这类：否则一次 401 会让 36 次运行全红，
#: 而测试仍然绿——「全红但绿」比直接失败更危险。
INFRA_STATUSES = ("error", "llm_error")


def real_config_or_skip():
    """真模型评测用的模型配置。

    `tests/conftest.py` 的 `model_env` 是 autouse 的，会给**每个**测试种一份
    `test-key` / `test-model` 的 BYOK 配置。那对 eval 是致命的：运行会全部 401
    （实测踩到过一次），所以这里显式挡一道——缺配置跳过，拿到夹具的假配置直接失败。
    """
    from avid.ai.byok import resolve_chat
    from avid.ai.config import ConfigError

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
    """断言没有仪器/配置级失败，返回报表文本供打印。"""
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
    """等运行线程把会话句柄交还。

    `record.terminal` 只说明"注册表已定终态"，而句柄是运行线程**紧接着**才交还的：
    终态事件之后还有 ``finally``（弹注册表、关句柄）。测试要自己 ``repo.open`` 直读
    文件时必须等这一条，否则会撞上 ``SessionAlreadyOpenError``——服务端读路径不走
    这条路（``SessionService._session`` 用"活动句柄回退 + 句柄锁"），所以它不受影响。

    这条竞态在阶段 22 之前就存在，只是窗口窄到看不出来；终态路径多了一次会话写入
    之后稳定复现（HEAD 10/10 通过、改动后约 70% 失败），于是把同步条件补对。
    """
    assert wait_for(
        lambda: services.runs.active_run_id(session_id) is None, timeout
    ), f"运行没有交还会话句柄：{session_id}"


def collect(services: Any, run_id: str, after: int = 0, deltas: bool = False) -> list:
    """把订阅到的帧收完（跳过心跳）。运行结束时生成器会自然结束。"""
    return [
        event
        for event in services.runs.subscribe(run_id, after=after, deltas=deltas)
        if event is not None
    ]


def create_session(client: Any, **fields: Any):
    """经 HTTP 新建会话——**总是带上工作区**。

    建会话必须显式指定归属，所以测试也要先问服务端"这个进程绑定了哪个工作地点"
    （`GET /api/workspaces` 的第一项就是它，`is_default=true`），再显式传回去。
    """
    listed = client.get("/api/workspaces").json()["workspaces"]
    assert listed, "服务端必须至少绑定一个工作地点"
    workspace = next((ws for ws in listed if ws.get("is_default")), listed[0])
    return client.post("/api/sessions", json={"workspace": workspace["id"], **fields})


def bound_workspace(services: Any) -> str:
    """进程绑定的工作区 id（测试里的"工作地点"）。"""
    return services.workspaces.default.id


def new_session(services: Any, name: str | None = None) -> str:
    return services.sessions.create(workspace=bound_workspace(services), name=name)["id"]
