"""图片输入的端点边界（阶段 59）。

隔离测试先列失败清单（§6），每条都有用例：
  ① 客户端声明的类型是假的（送 BMP 说是 PNG）；② base64 坏了；③ 单图超限；
  ④ 图片没进会话（消息形态变了）；⑤ 读侧把 base64 一起吐给浏览器（条目页 / SSE）；
  ⑥ 字节端点拿不回原字节、或拿错下标；⑦ 纯文本消息的形态被改了（老会话兼容）。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, create_session, make_turn, wait_for

from avid.attachments import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE, image_part
from avid.services import Services
from avid.web import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
BMP = b"BM" + b"\x00" * 32


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def user_entry(page: dict) -> dict:
    """条目页默认最新在前；这里要的是那条用户消息（带图的那条）。"""
    return next(
        entry
        for entry in page["entries"]
        if entry["type"] == "message" and (entry["message"] or {}).get("role") == "user"
    )


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


def run_with_images(client, chat, images, prompt="看这个"):
    session = create_session(client).json()
    response = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": prompt, "images": images, "auto_approve": True},
    )
    return session["id"], response


def test_a_run_carries_the_image_into_the_session(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id, started = run_with_images(client, None, [{"name": "shot.png", "data": b64(PNG)}])

    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    page = client.get(f"/api/sessions/{session_id}/entries").json()
    user = user_entry(page)
    kinds = [part["type"] for part in user["message"]["content"]]
    assert kinds == ["text", "image"]
    assert user["message"]["content"][0]["text"] == "看这个"


def test_the_entry_page_hands_out_refs_not_bytes(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id, started = run_with_images(client, None, [{"name": "shot.png", "data": b64(PNG)}])
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    page = client.get(f"/api/sessions/{session_id}/entries").json()
    user = user_entry(page)
    ref = user["message"]["content"][1]
    assert ref["index"] == 1
    assert ref["mime"] == "image/png"
    assert ref["name"] == "shot.png"
    assert ref["bytes"] == len(PNG)
    assert "data" not in ref


def test_the_event_carries_refs_not_bytes(bundle):
    client, services = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id, started = run_with_images(client, None, [{"name": "shot.png", "data": b64(PNG)}])
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    events = [event for event in services.runs.subscribe(run_id) if event is not None]
    payload = next(event.data["message"] for event in events if event.type == "user_message")
    assert "data" not in payload["content"][1]
    assert payload["content"][1]["index"] == 1


def test_the_attachment_endpoint_returns_the_original_bytes(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id, started = run_with_images(client, None, [{"name": "shot.png", "data": b64(PNG)}])
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    page = client.get(f"/api/sessions/{session_id}/entries").json()
    entry_id = user_entry(page)["entry_id"]

    got = client.get(f"/api/sessions/{session_id}/entries/{entry_id}/attachments/1")
    assert got.status_code == 200, got.text
    assert got.content == PNG
    assert got.headers["content-type"] == "image/png"


def test_a_wrong_index_is_rejected(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("看到了")))
    session_id, started = run_with_images(client, None, [{"name": "shot.png", "data": b64(PNG)}])
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")
    page = client.get(f"/api/sessions/{session_id}/entries").json()
    entry_id = user_entry(page)["entry_id"]

    response = client.get(f"/api/sessions/{session_id}/entries/{entry_id}/attachments/0")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_text_only_message_stays_a_plain_string(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好的")))
    session_id, started = run_with_images(client, None, [], prompt="就这一句")
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    page = client.get(f"/api/sessions/{session_id}/entries").json()
    assert user_entry(page)["message"]["content"] == "就这一句"


def test_a_declared_type_is_ignored_in_favor_of_the_bytes(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    _, started = run_with_images(client, None, [{"name": "fake.png", "data": b64(BMP)}])

    assert started.status_code == 400
    assert started.json()["error"]["code"] == "invalid_attachment"
    assert "只收" in started.json()["error"]["message"]


def test_broken_base64_is_rejected(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    _, started = run_with_images(client, None, [{"name": "x.png", "data": "not base64!!"}])

    assert started.status_code == 400
    assert "base64" in started.json()["error"]["message"]


def test_an_oversized_image_is_rejected_before_the_run_starts(bundle):
    client, services = bundle(chat=ScriptedChat(make_turn("好")))
    big = PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG) + 1)  # 刚好超一点：要拿到那条中文原因
    _, started = run_with_images(client, None, [{"name": "huge.png", "data": b64(big)}])

    assert started.status_code == 400
    assert started.json()["error"]["code"] == "invalid_attachment"
    assert services.runs.active_runs() == []  # 被拒的输入不该留下一个 run


def test_too_many_images_are_rejected(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("好")))
    images = [{"name": f"{i}.png", "data": b64(PNG)} for i in range(MAX_IMAGES_PER_MESSAGE + 1)]
    _, started = run_with_images(client, None, images)

    assert started.status_code == 400
    assert "最多" in started.json()["error"]["message"]


def test_a_stored_part_round_trips_through_the_vocabulary():
    """用例自己造块时走同一条词汇：ref 的下标与字节端点定位的是同一块。"""
    part = image_part(PNG, name="shot.png")
    assert part["bytes"] == len(PNG)
    assert part["mime"] == "image/png"

def test_a_model_declaring_no_vision_is_refused_before_the_run(bundle, tmp_path):
    """模型声明 capabilities.vision=false 时，带图的消息在起运行前就拒（阶段 55 的声明，阶段 59 接上）。

    发出去只会换来一个端点错误，会话里白留一条用户消息加一条失败记账。
    """
    import json
    import os
    from pathlib import Path

    config_path = Path(os.environ["AVID_BYOK_CONFIG"])
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["providers"][0]["models"] = [{"id": "blind", "capabilities": {"vision": False}}]
    raw["bindings"]["chat"] = "test/blind"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    client, services = bundle(chat=ScriptedChat(make_turn("好")))
    _, started = run_with_images(client, None, [{"name": "shot.png", "data": b64(PNG)}])

    assert started.status_code == 400
    assert started.json()["error"]["code"] == "invalid_attachment"
    assert "vision" in started.json()["error"]["message"]
    assert services.runs.active_runs() == []

    # 纯文本不受这条声明影响（约束只落在带图的消息上）
    session_id = create_session(client).json()["id"]
    plain = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "就一句话"})
    assert plain.status_code == 201, plain.text
