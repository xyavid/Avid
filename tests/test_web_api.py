"""B10 – B16 与端点契约：用 httpx/TestClient 打真端点。

覆盖：未知 ``/api`` 不回落到 SPA、一个会话一个 run、会话 CRUD、条目分页有界、
SSE 分帧与 ``Last-Event-ID`` 重放、事件契约与特性表、构建戳缺失时的行为。
"""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient
from support import (
    RecordingTools,
    ScriptedChat,
    collect,
    create_session,
    make_turn,
    tool_call,
    wait_for,
)

from avid.runtime import events
from avid.session import SessionRecorder
from avid.svc import API_VERSION, FEATURES, Services
from avid.web import create_app
from avid.web.schemas import classify_tool_status


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
        # 静态目录显式指向一个不存在的路径：否则默认目录里一旦有构建产物，
        # 断言就会随环境变化（现在是「未构建 → 503」的确定性用例）。
        return TestClient(
            create_app(services=instance, static_dir=sandbox / "static-not-built"),
            base_url="http://127.0.0.1:8765",
        ), instance

    yield build
    for instance in services:
        instance.close()


def finish_run(client: TestClient, chat, tools=None, prompt: str = "问题") -> tuple[str, str]:
    session = create_session(client).json()
    response = client.post(
        f"/api/sessions/{session['id']}/runs", json={"prompt": prompt, "auto_approve": True}
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")
    return session["id"], run_id


# ---------------- 信任边界（P1-21） ----------------


def test_host_outside_the_allowlist_is_rejected(bundle):
    """DNS rebinding：恶意域名解析到 127.0.0.1 时，浏览器认为它同源——Host 会露馅。"""
    client, _ = bundle()
    response = client.get("/api/meta", headers={"Host": "evil.example.com"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "host_rejected"


def test_cross_site_origin_is_rejected_even_without_a_body(bundle):
    """CSRF：无 body 的 POST 是"简单请求"，不做预检就能打到写端点。

    真实后果：任意网站都能让本机弹文件夹选择器（`/workspaces/pick`）或取消正在跑
    的任务（`/runs/{id}/cancel`）。跨源请求一定带 Origin，白名单外拒掉。
    """
    client, _ = bundle()

    rejected = client.post(
        "/api/workspaces/pick", headers={"Origin": "https://evil.example.com"}
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "origin_rejected"

    # 回环来源（界面自己的源）放行；命令行不带 Origin 也放行。
    # 用"不存在的 run"当探针：404 说明过了信任边界，而不是被 403 拦下。
    allowed = client.post(
        "/api/runs/run_nope/cancel",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert allowed.status_code == 404, allowed.text
    assert allowed.json()["error"]["code"] == "run_not_found"


def test_an_extra_host_can_be_allowed_explicitly(bundle, monkeypatch):
    """非回环部署的逃生口：`AVID_ALLOWED_HOSTS` 显式放行。"""
    monkeypatch.setenv("AVID_ALLOWED_HOSTS", "avid.internal:8765")
    client, _ = bundle()

    response = client.get("/api/meta", headers={"Host": "avid.internal:8765"})

    assert response.status_code == 200


# ---------------- B10 ----------------


def test_session_list_does_not_replay_the_sessions(bundle, monkeypatch):
    """列表页读名字与条数，但**不该**逐个重放整个会话。

    以前每个会话都 open() 一次（逐行重放 + 建对象 + 抢会话句柄）：20 个会话
    5.9 MB 实测 54 ms，会话一多首屏与"每次运行结束重取列表"都变成秒级。
    这里用"open 次数必须为 0"把快速路径钉住，同时断言三个字段仍然正确。
    """
    from avid.session import jsonl as session_jsonl

    client, services = bundle(chat=ScriptedChat(make_turn("答")))
    session_id, _ = finish_run(client, None)

    opened: list[str] = []
    real_open = session_jsonl.JsonlStorage.open

    def counting_open(path, **kwargs):
        opened.append(str(path))
        return real_open(path, **kwargs)

    monkeypatch.setattr(session_jsonl.JsonlStorage, "open", staticmethod(counting_open))

    listed = client.get("/api/sessions").json()["sessions"]

    assert opened == [], f"列表页不该打开会话：{opened}"
    entry = next(item for item in listed if item["id"] == session_id)
    assert entry["message_count"] >= 2  # 用户消息 + 助手消息
    assert entry["truncated_tail"] is False
    assert entry["active_run_id"] is None


def test_unknown_api_is_json_404(bundle):
    client, _ = bundle()
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "not_found"

    # 未构建静态资源时，根路径给出可执行的修复提示而不是 500
    root = client.get("/")
    assert root.status_code == 503
    assert root.json()["error"]["code"] == "static_missing"


# ---------------- B11 ----------------


def test_one_run_per_session(bundle):
    entered, release = threading.Event(), threading.Event()

    def blocking_chat(config, messages, **kwargs):
        entered.set()
        release.wait(5.0)
        return make_turn("结束")

    client, _ = bundle(blocking_chat)
    session = create_session(client).json()

    first = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "一"})
    assert entered.wait(5.0)
    second = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "二"})
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "run_busy"
    assert "run_id" in first.json()
    release.set()
    assert wait_for(
        lambda: client.get(f"/api/runs/{first.json()['run_id']}").json()["status"] == "finished"
    )


def test_run_for_unknown_session_is_404(bundle):
    client, _ = bundle()
    response = client.post("/api/sessions/nope/runs", json={"prompt": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


# ---------------- 会话 CRUD ----------------


def test_session_lifecycle(bundle):
    client, _ = bundle()
    created = create_session(client, name="演示会话")
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert created.json()["name"] == "演示会话"

    duplicate = create_session(client, id=session_id)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "session_exists"

    listed = client.get("/api/sessions").json()["sessions"]
    assert [item["id"] for item in listed] == [session_id]

    renamed = client.patch(f"/api/sessions/{session_id}", json={"name": "改名了"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "改名了"

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["message_count"] == 0 and detail["active_run_id"] is None

    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_delete_is_blocked_by_active_run(bundle):
    entered, release = threading.Event(), threading.Event()

    def blocking_chat(config, messages, **kwargs):
        entered.set()
        release.wait(5.0)
        return make_turn("结束")

    client, _ = bundle(blocking_chat)
    session = create_session(client).json()
    client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "一"})
    assert entered.wait(5.0)

    blocked = client.delete(f"/api/sessions/{session['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "session_busy"
    release.set()


# ---------------- B12 ----------------


def test_every_durable_message_event_maps_to_one_entry(bundle):
    tools = RecordingTools().registry("read_file")
    chat = ScriptedChat(
        make_turn("", [tool_call("read_file", '{"path": "a"}')]), make_turn("结论")
    )
    client, services = bundle(chat, tools)
    session_id, run_id = finish_run(client, chat, tools)

    pages = client.get(f"/api/sessions/{session_id}/entries").json()
    entries = {item["entry_id"]: item for item in pages["entries"]}
    assert len(entries) == 4  # user, assistant(tool_calls), tool, assistant

    message_events = [
        event
        for event in collect(services, run_id)
        if event.type
        in (events.USER_MESSAGE, events.ASSISTANT_MESSAGE, events.TOOL_RESULT_MESSAGE)
    ]
    assert len(message_events) == len(entries)
    for event in message_events:
        entry = entries[event.data["entry_id"]]
        assert entry["message"] == event.data["message"]
        assert entry["seq"] > 0


# ---------------- B13 ----------------


def test_meta_matches_kernel_and_features_match_endpoints(bundle):
    client, _ = bundle()
    meta = client.get("/api/meta").json()
    assert meta["event_types"] == list(events.EVENT_TYPES)
    assert meta["features"] == FEATURES
    assert meta["api_version"] == API_VERSION
    assert meta["capabilities"]["model"] == "test-model"
    assert "bash" in meta["capabilities"]["tools"]
    assert isinstance(meta["capabilities"]["skills"], list)
    assert meta["stream"]["heartbeat_seconds"] > 0

    # 特性表声明的能力必须真的有端点
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/skills").status_code == 200
    if FEATURES["approvals"]:
        assert client.get("/api/runs/run_x/approvals").status_code == 404  # 存在但 run 未知
    if FEATURES["cancel"]:
        assert client.post("/api/runs/run_x/cancel").status_code == 404
    if FEATURES["deltas"]:
        # 声明可用就得真的可用：端点接受 ?deltas=1（未知 run 仍是 404，说明路由在）
        assert client.get("/api/runs/run_x/events?deltas=1").status_code == 404
    else:
        assert "assistant_delta" in meta["event_types"]  # 类型已定义，只是不投递
    if FEATURES["workspaces"]:
        # 工作区是**端点型**特性：路由在就返回列表。
        listed = client.get("/api/workspaces")
        assert listed.status_code == 200
        assert listed.json()["workspaces"]
    if FEATURES["permission_modes"]:
        # 权限模式是**参数型**特性，没有新端点可断言；用"非法值被拒"证明它真的生效
        # （声明了却没人读，就会连非法值都照收）。
        session = create_session(client).json()
        rejected = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "x", "permission": "yolo"},
        )
        assert rejected.status_code == 422


def test_health(bundle):
    client, _ = bundle()
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["api_version"] == API_VERSION
    assert body["uptime_ms"] >= 0


# ---------------- F4：分支端点 ----------------


def test_branch_endpoints_list_fork_and_reject_conflicts(bundle):
    client, _ = bundle(chat=ScriptedChat(make_turn("主线"), make_turn("分支上")))
    session_id = create_session(client, name="分支").json()["id"]

    fresh = client.get(f"/api/sessions/{session_id}/branches").json()
    assert [item["name"] for item in fresh["branches"]] == ["main"]
    assert fresh["branches"][0]["is_default"] is True
    assert fresh["branches"][0]["tip_entry_id"] is None
    assert fresh["branches"][0]["entry_count"] == 0

    started = client.post(
        f"/api/sessions/{session_id}/runs", json={"prompt": "跑一下", "auto_approve": True}
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    entries = client.get(f"/api/sessions/{session_id}/entries?order=asc").json()["entries"]
    assert len(entries) == 2
    fork_at = entries[1]["entry_id"]

    created = client.post(f"/api/sessions/{session_id}/branches", json={"at": fork_at})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "b2"
    assert body["tip_entry_id"] == fork_at
    assert body["entry_count"] == 2
    assert body["is_default"] is False

    # 重名 409（悄悄重建会丢掉原来那条链）；未知分叉点 400（它只是请求体里的一个坏值）
    duplicate = client.post(f"/api/sessions/{session_id}/branches", json={"name": "b2"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "branch_exists"
    assert client.post(f"/api/sessions/{session_id}/branches", json={"at": "e_missing"}).status_code == 400

    # 在分支上起运行：请求体带 branch，main 的链条一个字都不变
    def main_chain() -> list[str]:
        page = client.get(
            f"/api/sessions/{session_id}/entries?branch=main&order=asc"
        ).json()
        return [entry["entry_id"] for entry in page["entries"]]

    main_before = main_chain()
    branch_run = client.post(
        f"/api/sessions/{session_id}/runs",
        json={"prompt": "在分支上继续", "auto_approve": True, "branch": "b2"},
    )
    assert branch_run.status_code == 201, branch_run.text
    branch_run_id = branch_run.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{branch_run_id}").json()["status"] == "finished")

    assert main_chain() == main_before
    side = client.get(f"/api/sessions/{session_id}/entries?branch=b2&order=asc").json()["entries"]
    assert [entry["entry_id"] for entry in side[:2]] == main_before
    assert len(side) == 4

    listed = {
        item["name"]: item
        for item in client.get(f"/api/sessions/{session_id}/branches").json()["branches"]
    }
    assert set(listed) == {"main", "b2"}
    assert listed["b2"]["entry_count"] == 4


# ---------------- B16 与分页 ----------------


def fill(sandbox, services: Services, session_id: str, count: int) -> None:
    metadata = services.runs.find_metadata(session_id)
    session = services.repo.open(metadata)
    try:
        recorder = SessionRecorder(session)
        recorder.ensure_branch()
        for index in range(count):
            recorder.on_message({"role": "assistant", "content": f"m{index}"})
    finally:
        session.close()


def test_entries_are_bounded_by_default_and_by_cap(bundle):
    client, services = bundle()
    session_id = create_session(client).json()["id"]
    fill(None, services, session_id, 600)

    default = client.get(f"/api/sessions/{session_id}/entries").json()
    assert len(default["entries"]) == 100
    assert default["limit"] == 100 and default["has_more"] is True
    assert default["next_cursor"] == default["entries"][-1]["seq"]

    capped = client.get(
        f"/api/sessions/{session_id}/entries", params={"limit": 10000}
    ).json()
    assert capped["limit"] == 500
    assert len(capped["entries"]) == 500
    assert capped["has_more"] is True

    nope = client.get(
        f"/api/sessions/{session_id}/entries", params={"order": "sideways"}
    )
    assert nope.status_code == 422
    assert nope.json()["error"]["code"] == "invalid_schema"


def test_entries_pagination_walks_the_chain(bundle):
    client, services = bundle()
    session_id = create_session(client).json()["id"]
    fill(None, services, session_id, 150)

    first = client.get(f"/api/sessions/{session_id}/entries").json()
    second = client.get(
        f"/api/sessions/{session_id}/entries",
        params={"cursor_seq": first["next_cursor"]},
    ).json()

    assert len(first["entries"]) == 100
    assert len(second["entries"]) == 50
    assert second["has_more"] is False
    assert {item["entry_id"] for item in first["entries"]} & {
        item["entry_id"] for item in second["entries"]
    } == set()

    # asc 与 desc 是同一批条目的两个方向
    ascending = client.get(
        f"/api/sessions/{session_id}/entries", params={"order": "asc", "limit": 500}
    ).json()
    assert [item["seq"] for item in ascending["entries"]] == sorted(
        item["seq"] for item in ascending["entries"]
    )


def test_truncated_tail_is_derived_not_persisted(bundle):
    client, services = bundle()
    session_id = create_session(client).json()["id"]
    metadata = services.runs.find_metadata(session_id)
    session = services.repo.open(metadata)
    try:
        recorder = SessionRecorder(session)
        recorder.ensure_branch()
        recorder.on_message({"role": "user", "content": "问题"})
        recorder.on_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            }
        )
    finally:
        session.close()

    assert client.get(f"/api/sessions/{session_id}/entries").json()["truncated_tail"] is True
    assert client.get(f"/api/sessions/{session_id}").json()["truncated_tail"] is True

    session = services.repo.open(services.runs.find_metadata(session_id))
    try:
        SessionRecorder(session).ensure_branch().append_message(
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
        )
    finally:
        session.close()
    assert client.get(f"/api/sessions/{session_id}/entries").json()["truncated_tail"] is False


# ---------------- SSE ----------------


def test_sse_frames_durable_events_with_ids(bundle):
    tools = RecordingTools().registry("read_file")
    chat = ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好了"))
    client, _ = bundle(chat, tools)
    session_id, run_id = finish_run(client, chat, tools)

    response = client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text

    assert "id: 1\n" in body
    assert "event: run_started" in body
    assert "event: run_finished" in body
    assert body.endswith("\n\n")

    # 第一帧的 data 是合法 JSON 且带 run_id / session_id / seq / type / data
    first = body.split("\n\n")[0]
    lines = dict(
        line.split(": ", 1) for line in first.splitlines() if ": " in line
    )
    payload = json.loads(lines["data"])
    assert lines["id"] == "1"
    assert payload["run_id"] == run_id
    assert payload["session_id"] == session_id
    assert payload["seq"] == 1
    assert payload["type"] == "run_started"
    assert payload["data"]["prompt"] == "问题"


def test_sse_replays_after_last_event_id(bundle):
    tools = RecordingTools().registry("read_file")
    chat = ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好了"))
    client, _ = bundle(chat, tools)
    _, run_id = finish_run(client, chat, tools)

    response = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
    lines = [line for line in response.text.splitlines() if line.startswith("id: ")]
    assert lines[0] == "id: 3"
    assert "id: 2\n" not in response.text

    # 显式 ?after= 优先于 header
    explicit = client.get(
        f"/api/runs/{run_id}/events", params={"after": 0}, headers={"Last-Event-ID": "2"}
    )
    assert "id: 1\n" in explicit.text

    assert client.get("/api/runs/run_nope/events").status_code == 404


# ---------------- 工具状态判定单点 ----------------


def test_classify_tool_status_is_single_point(bundle):
    assert classify_tool_status("ok") == "ok"
    assert classify_tool_status("错误：找不到文件") == "failed"
    assert classify_tool_status("工具 bash 执行失败：boom") == "failed"
    assert classify_tool_status("Permission denied.", denied_kind="user") == "denied"
    assert classify_tool_status("很长" * 10, truncated=True) == "truncated"


def test_tool_status_reaches_the_wire(bundle):
    tools = RecordingTools({"read_file": "错误：文件不存在"}).registry("read_file")
    chat = ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    client, _ = bundle(chat, tools)
    _, run_id = finish_run(client, chat, tools)

    body = client.get(f"/api/runs/{run_id}/events").text
    finished = [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ") and '"tool_call_finished"' in line
    ]
    assert finished and finished[0]["data"]["status"] == "failed"
    assert finished[0]["data"]["content_chars"] > 0
    assert "content" not in finished[0]["data"]


# ---------------- 静态与 SPA fallback（§4.3） ----------------


def test_spa_fallback_serves_index_but_missing_assets_are_404(sandbox):
    static = sandbox / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    (static / "assets" / "app-abc123.js").write_text("export const x = 1\n", encoding="utf-8")

    services = Services(root=sandbox / ".avid" / "sessions")
    client = TestClient(
        create_app(services=services, static_dir=static),
        base_url="http://127.0.0.1:8765",
    )
    try:
        # 深链接回落到 SPA 外壳（前端路由接管）
        deep = client.get("/settings")
        assert deep.status_code == 200 and "SPA" in deep.text

        # 真实静态文件按原类型返回
        asset = client.get("/assets/app-abc123.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]

        # 带扩展名但不存在 → 显式 404（不能让浏览器拿 HTML 当 JS）
        missing = client.get("/assets/stale-000.js")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "asset_not_found"
    finally:
        services.close()
