"""补充输入的端点（阶段 60）：投一条 / 看队列 / 撤销 / 领取起 run。

隔离测试先列失败清单（§6），每条都有用例：
  ① 空闲投 now 该直接起 run（不是排队）；② 忙时投 now 该在下一个 step 交付；
  ③ run 结束前没赶上的 now 该降级为排队（不能消失）；④ 重复 client_id 只收一条；
  ⑤ 领取是原子的（第二次领取报错，不是静默无事）；⑥ 空输入拒绝；
  ⑦ 撤销只动未领取的；⑧ 排队项自带开关（领取时不必重发）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, create_session, make_turn, tool_call, wait_for

from avid.services import Services
from avid.web import create_app


@pytest.fixture
def bundle(sandbox):
    services: list[Services] = []

    def build(chat=None, tools=None, **kwargs):
        instance = Services(
            root=sandbox / ".avid" / "sessions",
            chat=chat,
            tool_registry=tools,
            **kwargs,
        )
        services.append(instance)
        return TestClient(
            create_app(services=instance, static_dir=sandbox / "static-not-built"),
            base_url="http://127.0.0.1:8765",
        ), instance

    yield build
    for instance in services:
        instance.close()


def submit(client, session_id, **body):
    body.setdefault("prompt", "补充一句")
    return client.post(f"/api/sessions/{session_id}/inputs", json=body)


def inputs_of(client, session_id) -> list[dict]:
    return client.get(f"/api/sessions/{session_id}/inputs").json()["inputs"]


# ---------------- 投递与两种 mode ----------------


def test_after_parks_the_input_for_the_next_turn(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("答")))
    session_id = create_session(client).json()["id"]

    accepted = submit(client, session_id, mode="after", prompt="下一件事")

    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    assert body["kind"] == "input" and body["mode"] == "after" and body["run_id"] is None
    queued = inputs_of(client, session_id)
    assert [item["text"] for item in queued] == ["下一件事"]
    assert queued[0]["missed"] is False


def test_now_while_idle_starts_a_run_right_away(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id = create_session(client).json()["id"]

    accepted = submit(client, session_id, mode="now", prompt="现在就做")

    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    assert body["kind"] == "run" and body["run_id"] is not None
    run_id = body["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")
    # 它不是一个排队项：内容已经作为这条 run 的输入落进了会话
    assert inputs_of(client, session_id) == []
    entries = client.get(f"/api/sessions/{session_id}/entries").json()["entries"]
    assert any(
        entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == "现在就做"
        for entry in entries
    )


def test_now_while_busy_is_delivered_at_the_next_step(bundle):
    """工具批跑着的时候投的补充：不该等这一轮结束，而是下一个模型请求之前进去。"""
    def slow_bash(arguments, **kwargs):
        time.sleep(0.4)
        return "命令跑完了"

    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "echo hi"}')]),
        make_turn("收到补充"),
    )
    client, services = bundle(chat=chat, tools={"bash": slow_bash})
    session_id = create_session(client).json()["id"]
    started = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "开始"})
    run_id = started.json()["run_id"]
    assert wait_for(
        lambda: any(
            event is not None and event.type == "tool_call_started"
            for event in services.runs.subscribe(run_id)
        ),
        5,
    )

    accepted = submit(client, session_id, mode="now", prompt="先别改代码")

    assert accepted.status_code == 201, accepted.text
    assert accepted.json() == {
        "kind": "input",
        "input_id": accepted.json()["input_id"],
        "run_id": None,
        "mode": "now",
    }
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished", 10)
    # 第二个模型请求里看得到它，而且是在工具结果之后
    second = chat.requests[1]["messages"]
    texts = [message.get("content") for message in second if message.get("role") == "user"]
    assert "先别改代码" in texts
    assert texts.index("先别改代码") > 0


def test_a_steer_that_missed_the_run_becomes_queued(bundle):
    """没赶上的 now 降级为排队项并标 missed——被接受的输入不消失。

    用取消来造这个窗口：取消检查点在轮次开头（领取之前），所以这条补充一定没被领走。
    """
    def slow_bash(arguments, **kwargs):
        time.sleep(0.4)
        return "命令跑完了"

    chat = ScriptedChat(make_turn("", [tool_call("bash", '{"command": "echo hi"}')]))
    client, services = bundle(chat=chat, tools={"bash": slow_bash})
    session_id = create_session(client).json()["id"]
    started = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "开始"})
    run_id = started.json()["run_id"]
    assert wait_for(
        lambda: any(
            event is not None and event.type == "tool_call_started"
            for event in services.runs.subscribe(run_id)
        ),
        5,
    )

    accepted = submit(client, session_id, mode="now", prompt="没赶上")
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["mode"] == "now"

    client.post(f"/api/runs/{run_id}/cancel")
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "cancelled", 10)
    queued = inputs_of(client, session_id)
    assert [item["input_id"] for item in queued] == [accepted.json()["input_id"]]
    assert queued[0]["mode"] == "after" and queued[0]["missed"] is True


# ---------------- 幂等、撤销、领取 ----------------


def test_the_same_client_id_is_not_accepted_twice(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("答")))
    session_id = create_session(client).json()["id"]

    first = submit(client, session_id, mode="after", prompt="只发一次", client_id="c-1")
    again = submit(client, session_id, mode="after", prompt="只发一次", client_id="c-1")

    assert first.json()["input_id"] == again.json()["input_id"]
    assert len(inputs_of(client, session_id)) == 1


def test_a_queued_input_is_claimed_once(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("做完了")))
    session_id = create_session(client).json()["id"]
    item = submit(client, session_id, mode="after", prompt="下一件事").json()

    claimed = client.post(
        f"/api/sessions/{session_id}/runs", json={"from_input": item["input_id"]}
    )

    assert claimed.status_code == 201, claimed.text
    run_id = claimed.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")
    assert inputs_of(client, session_id) == []
    again = client.post(
        f"/api/sessions/{session_id}/runs", json={"from_input": item["input_id"]}
    )
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_request"


def test_a_queued_input_carries_its_own_switches(bundle):
    """排队项自带投递时的意图：领取时不必重发，也就不会漂移。"""
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    session_id = create_session(client).json()["id"]
    item = submit(
        client, session_id, mode="after", prompt="带开关的", model="test/test-model"
    ).json()

    claimed = client.post(
        f"/api/sessions/{session_id}/runs", json={"from_input": item["input_id"]}
    )

    assert claimed.status_code == 201, claimed.text
    run_id = claimed.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")


def test_dropping_a_queued_input(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    session_id = create_session(client).json()["id"]
    item = submit(client, session_id, mode="after", prompt="算了").json()

    assert client.delete(f"/api/sessions/{session_id}/inputs/{item['input_id']}").status_code == 204
    assert inputs_of(client, session_id) == []
    # 再撤一次：它已经不在了（不是静默成功）
    assert client.delete(f"/api/sessions/{session_id}/inputs/{item['input_id']}").status_code == 400


def test_an_empty_input_is_rejected(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    session_id = create_session(client).json()["id"]

    response = submit(client, session_id, mode="after", prompt="   ")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_an_unknown_mode_is_rejected(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    session_id = create_session(client).json()["id"]

    response = submit(client, session_id, mode="someday", prompt="以后")

    assert response.status_code == 422  # DTO 的 Literal 先拦
    assert inputs_of(client, session_id) == []


def test_inputs_carry_images_like_any_other_message(bundle):
    """补充输入与首条消息共用同一条内容链路（阶段 59 的 parts 在这里同样成立）。"""
    import base64

    client, _ = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id = create_session(client).json()["id"]
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")

    accepted = submit(
        client, session_id, mode="now", prompt="看这张", images=[{"name": "s.png", "data": png}]
    )

    assert accepted.status_code == 201, accepted.text
    run_id = accepted.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished", 10)
    entries = client.get(f"/api/sessions/{session_id}/entries").json()["entries"]
    user = next(
        entry for entry in entries if entry["type"] == "message" and entry["message"]["role"] == "user"
    )
    assert [part["type"] for part in user["message"]["content"]] == ["text", "image"]
