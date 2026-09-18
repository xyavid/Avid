"""B4 / B5 / B6 / B7：审批挂起-答复-恢复、幂等、超时失败关闭、取消粒度。

审批是**请求/响应**语义，所以这里走真的 HTTP 往返（``TestClient``），工具换成
记录器——「工具到底执行了几次」必须与事件计数双断言。
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from avid.runtime import events
from avid.svc import Services
from avid.web import create_app
from support import (
    create_session,
    RecordingTools,
    ScriptedChat,
    collect,
    make_turn,
    tool_call,
    wait_for,
)


class GateChat:
    """第一次调用阻塞，直到测试放行——用来把「取消发生在模型调用期间」变成确定性事实。"""

    def __init__(self, turn, entered: threading.Event, release: threading.Event) -> None:
        self.turn = turn
        self.entered = entered
        self.release = release
        self.calls = 0

    def __call__(self, config, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            assert self.release.wait(5.0), "测试没有放行模型调用"
            return self.turn
        return make_turn("结束")


@pytest.fixture
def make_client(sandbox):
    clients: list[Services] = []

    def build(chat, tools=None, **kwargs):
        services = Services(
            root=sandbox / ".avid" / "sessions",
            chat=chat,
            tool_registry=tools,
            **kwargs,
        )
        clients.append(services)
        return TestClient(create_app(services=services)), services

    yield build
    for services in clients:
        services.close()


def start_run(client: TestClient, prompt: str = "跑一下", **body) -> tuple[str, str]:
    session = create_session(client).json()
    response = client.post(
        f"/api/sessions/{session['id']}/runs", json={"prompt": prompt, **body}
    )
    assert response.status_code == 201, response.text
    return session["id"], response.json()["run_id"]


def run_status(client: TestClient, run_id: str) -> str:
    return client.get(f"/api/runs/{run_id}").json()["status"]


def wait_run(client: TestClient, run_id: str, status: str, timeout: float = 5.0) -> bool:
    return wait_for(lambda: run_status(client, run_id) == status, timeout)


# ---------------- B4 ----------------


def test_approval_suspends_then_resumes(make_client):
    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]), make_turn("做完了")
    )
    tools = RecordingTools()
    client, services = make_client(chat, tools.registry("bash"))

    session_id, run_id = start_run(client)
    assert wait_run(client, run_id, "awaiting_approval"), run_status(client, run_id)

    pending = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"]
    assert len(pending) == 1
    assert pending[0]["tool"] == "bash"
    assert pending[0]["arguments"] == {"command": "echo hi"}
    assert pending[0]["expires_at"] > pending[0]["created_at"]
    assert tools.calls == [], "待决审批期间工具不该执行"

    answer = client.post(
        f"/api/runs/{run_id}/approvals/{pending[0]['approval_id']}",
        json={"decision": "allow"},
    )
    assert answer.status_code == 200 and answer.json()["accepted"] is True

    assert wait_for(lambda: run_status(client, run_id) == "finished"), run_status(client, run_id)
    assert tools.calls == [("bash", {"command": "echo hi"})]

    # seq 连续无洞
    got = collect(services, run_id)
    seqs = [event.seq for event in got if event.seq is not None]
    assert seqs == list(range(1, len(seqs) + 1))
    types = [event.type for event in got]
    assert events.APPROVAL_REQUESTED in types and events.APPROVAL_RESOLVED in types
    resolved = [e for e in got if e.type == events.APPROVAL_RESOLVED][0]
    assert resolved.data["decision"] == "allow"
    # 运行结束后待决表清空
    assert client.get(f"/api/runs/{run_id}/approvals").json()["approvals"] == []


def test_deny_stops_the_tool_message(make_client):
    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]), make_turn("好，我换个办法")
    )
    tools = RecordingTools()
    client, services = make_client(chat, tools.registry("bash"))

    _, run_id = start_run(client)
    assert wait_run(client, run_id, "awaiting_approval")
    approval = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"][0]
    client.post(
        f"/api/runs/{run_id}/approvals/{approval['approval_id']}",
        json={"decision": "deny"},
    )
    assert wait_for(lambda: run_status(client, run_id) == "finished")
    assert tools.calls == []

    got = collect(services, run_id)
    assert events.TOOL_CALL_DENIED in [event.type for event in got]
    denied = [event for event in got if event.type == events.TOOL_CALL_DENIED][0]
    assert denied.data["kind"] == "user"
    assert any(event.type == events.TOOL_RESULT_MESSAGE for event in got)


# ---------------- B5 ----------------


def test_repeated_answer_does_not_approve_twice(make_client):
    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]), make_turn("完成")
    )
    tools = RecordingTools()
    client, services = make_client(chat, tools.registry("bash"))

    _, run_id = start_run(client)
    assert wait_run(client, run_id, "awaiting_approval")
    approval_id = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"][0][
        "approval_id"
    ]
    url = f"/api/runs/{run_id}/approvals/{approval_id}"

    first = client.post(url, json={"decision": "allow"})
    second = client.post(url, json={"decision": "allow"})

    assert first.json()["accepted"] is True
    assert second.status_code == 200
    assert second.json()["accepted"] is False
    assert second.json()["already"] == "allow"

    assert wait_for(lambda: run_status(client, run_id) == "finished")
    assert len(tools.calls) == 1, "重复答复绝不二次批准"

    got = collect(services, run_id)
    assert len([e for e in got if e.type == events.TOOL_CALL_STARTED]) == 1
    assert len([e for e in got if e.type == events.APPROVAL_RESOLVED]) == 1


def test_conflicting_answer_is_409_and_unknown_is_404(make_client):
    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]), make_turn("完成")
    )
    tools = RecordingTools()
    client, _ = make_client(chat, tools.registry("bash"))

    _, run_id = start_run(client)
    assert wait_run(client, run_id, "awaiting_approval")
    approval_id = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"][0][
        "approval_id"
    ]
    url = f"/api/runs/{run_id}/approvals/{approval_id}"

    client.post(url, json={"decision": "allow"})
    conflict = client.post(url, json={"decision": "deny"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "approval_resolved"

    missing = client.post(
        f"/api/runs/{run_id}/approvals/ap_nope", json={"decision": "allow"}
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "approval_not_found"

    assert wait_for(lambda: run_status(client, run_id) == "finished")


# ---------------- B6 ----------------


def test_timeout_fails_closed(make_client):
    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]), make_turn("结束了")
    )
    tools = RecordingTools()
    client, services = make_client(chat, tools.registry("bash"), approval_timeout=0.1)

    _, run_id = start_run(client)
    assert wait_for(lambda: run_status(client, run_id) == "finished", timeout=5.0)
    assert tools.calls == [], "超时一律收敛为拒绝"

    got = collect(services, run_id)
    resolved = [e for e in got if e.type == events.APPROVAL_RESOLVED][0]
    assert resolved.data["decision"] == "deny"
    assert resolved.data["reason"] == "timeout"
    assert events.TOOL_CALL_DENIED in [event.type for event in got]

    # 过期后再答复 → 410
    approval_id = resolved.data["approval_id"]
    late = client.post(
        f"/api/runs/{run_id}/approvals/{approval_id}", json={"decision": "allow"}
    )
    assert late.status_code == 410
    assert late.json()["error"]["code"] == "approval_expired"


# ---------------- B7 ----------------


def test_cancel_does_not_lose_messages_nor_fabricate_results(make_client):
    entered, release = threading.Event(), threading.Event()
    chat = GateChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]), entered, release
    )
    tools = RecordingTools()
    client, services = make_client(chat, tools.registry("bash"))

    session_id, run_id = start_run(client)
    assert entered.wait(5.0), "模型调用没有开始"

    cancel = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel.status_code == 202
    assert cancel.json()["cancel_requested"] is True
    release.set()

    assert wait_for(lambda: run_status(client, run_id) == "cancelled"), run_status(client, run_id)
    assert tools.calls == [], "取消不产生伪造的工具结果"

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["message_count"] == 2, "触发用户消息 + 已产生的 assistant 消息"

    got = collect(services, run_id)
    types = [event.type for event in got]
    assert types[-1] == events.RUN_CANCELLED
    assert events.TOOL_CALL_STARTED not in types
    assert events.TOOL_RESULT_MESSAGE not in types
    seqs = [event.seq for event in got if event.seq is not None]
    assert seqs == list(range(1, len(seqs) + 1))
