"""B10 - B16 and endpoint contracts, exercised through httpx/TestClient.

Covers unknown /api paths not falling back to the SPA, one run per session, session CRUD,
bounded entry pagination, SSE framing and Last-Event-ID replay, the event/feature contract,
and the behavior when no static build exists.
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

from avid.agent.events import ASSISTANT_MESSAGE, EVENT_TYPES, TOOL_RESULT_MESSAGE, USER_MESSAGE
from avid.services import API_VERSION, FEATURES, Services
from avid.session import SessionRecorder
from avid.session.types import NOTICE_ENTRY
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
        # static_dir points at a nonexistent path: a real build in the default directory would
        # make the 503 assertions environment-dependent.
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


# ---------------- event stream concurrency slots ----------------


def test_stream_slots_refuse_over_the_limit_and_release(bundle):
    """An exhausted slot answers 503 and release restores it: a sync generator's next() blocks
    until the next event or heartbeat, holding a Starlette pool thread the whole time.
    """
    from avid.services import StreamSlots

    slots = StreamSlots(limit=2)
    assert slots.acquire() and slots.acquire()
    assert slots.active == 2
    assert not slots.acquire(), "额度用尽必须拒绝"
    assert slots.active == 2, "被拒的请求不该占额度"

    slots.release()
    assert slots.active == 1
    assert slots.acquire()

    slots.release()
    slots.release()
    slots.release()  # extra releases must not drive the count negative
    assert slots.active == 0


def test_an_over_limit_event_stream_gets_a_503(bundle, monkeypatch):
    """With the limit at 0 every stream answers 503 too_many_streams instead of queueing."""
    from avid.services import TooManyStreams

    client, services = bundle(chat=ScriptedChat(make_turn("答")))
    _, run_id = finish_run(client, None)

    services.streams.limit = 0
    response = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == TooManyStreams.code


def test_a_finished_stream_gives_its_slot_back(bundle):
    """A finished (or disconnected) stream must give its slot back, or the leak exhausts it."""
    client, services = bundle(chat=ScriptedChat(make_turn("答")))
    _, run_id = finish_run(client, None)
    services.streams.limit = 1

    # first connection: read to the end so the generator runs its finally
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        list(response.iter_lines())
    assert services.streams.active == 0, "流结束后额度没归还"

    # so a second connection can still open (the limit is 1)
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert next(response.iter_lines(), None) is not None


# ---------------- error surface leaks no internals ----------------


def test_internal_errors_only_expose_a_correlation_id(bundle):
    """A 500 returns only a code plus a correlation id: exception type and message (possibly
    absolute paths) must not reach the client."""
    from fastapi.testclient import TestClient

    from avid.web import create_app

    _, services = bundle()

    def boom():
        raise RuntimeError("内部细节：/home/someone/secret/path.py 打不开")

    # make one endpoint raise deterministically by replacing meta
    services.meta = boom  # type: ignore[method-assign]

    # The bundle client re-raises (test mode); this case wants what a real deployment returns,
    # so build one with raise_server_exceptions=False.
    client = TestClient(
        create_app(services=services, static_dir="/tmp/unbuilt"),
        base_url="http://127.0.0.1:8765",
        raise_server_exceptions=False,
    )
    response = client.get("/api/meta")

    assert response.status_code == 500
    payload = response.json()["error"]
    assert payload["code"] == "internal"
    assert "secret" not in response.text and "RuntimeError" not in response.text
    assert payload["detail"]["error_id"]


def test_security_headers_are_set_on_every_response(bundle):
    """CSP / Referrer-Policy / nosniff on every response, matching the no-inline-script build."""
    client, _ = bundle()

    for path in ("/api/meta", "/api/nope"):
        headers = client.get(path).headers
        assert headers["content-security-policy"].startswith("default-src 'self'")
        assert "script-src 'self'" in headers["content-security-policy"]
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-content-type-options"] == "nosniff"


# ---------------- input surface: length caps ----------------


def test_oversized_inputs_are_rejected_at_the_schema(bundle):
    """Oversized bodies are rejected at the schema layer with 422 before the service layer:
    no single request may write an arbitrarily large string.
    """
    from avid.web.schemas import MAX_NAME_CHARS, MAX_PROMPT_CHARS

    client, _ = bundle()
    workspace = client.get("/api/workspaces").json()["workspaces"][0]

    too_long_name = client.post(
        "/api/sessions",
        json={"workspace": workspace["id"], "name": "x" * (MAX_NAME_CHARS + 1)},
    )
    assert too_long_name.status_code == 422, too_long_name.text

    created = client.post(
        "/api/sessions", json={"workspace": workspace["id"], "name": "正常名字"}
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    too_long_prompt = client.post(
        f"/api/sessions/{session_id}/runs",
        json={"prompt": "x" * (MAX_PROMPT_CHARS + 1)},
    )
    assert too_long_prompt.status_code == 422, too_long_prompt.status_code

    # normal lengths still pass: the cap only stops input no human would type
    ok = client.post(
        f"/api/sessions/{session_id}/runs",
        json={"prompt": "正常问题", "auto_approve": True},
    )
    assert ok.status_code == 201


# ---------------- disk IO on read-only endpoints ----------------


def test_meta_caches_the_skills_scan(bundle, monkeypatch):
    """The skills scan behind /api/meta has a short TTL cache: the UI polls it repeatedly."""
    from avid.agent import skills as skills_module

    scans: list[int] = []
    real_scan = skills_module.SkillLoader.scan

    def counting_scan(self):
        scans.append(1)
        return real_scan(self)

    monkeypatch.setattr(skills_module.SkillLoader, "scan", counting_scan)
    client, _ = bundle()

    for _ in range(3):
        assert client.get("/api/meta").status_code == 200
    assert len(scans) == 1, f"三次 meta 扫了 {len(scans)} 次技能目录"


def test_event_stream_does_not_read_the_meta_endpoint(bundle, monkeypatch):
    """An SSE connection must not call services.meta() just for the heartbeat constant."""
    client, services = bundle(chat=ScriptedChat(make_turn("答")))
    _, run_id = finish_run(client, None)

    def forbidden(self):
        raise AssertionError("事件流不该调 meta()")

    monkeypatch.setattr(type(services), "meta", forbidden)

    with client.stream("GET", f"/api/runs/{run_id}/events", params={"after": 0}) as response:
        assert response.status_code == 200
        assert next(response.iter_lines(), None) is not None


def test_health_does_not_read_the_meta_endpoint(bundle, monkeypatch):
    client, services = bundle()

    def forbidden(self):
        raise AssertionError("健康探针不该调 meta()")

    monkeypatch.setattr(type(services), "meta", forbidden)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["api_version"] == 1


# ---------------- trust boundary ----------------


def test_host_outside_the_allowlist_is_rejected(bundle):
    """DNS rebinding: a hostile name resolving to 127.0.0.1 looks same-origin to the browser,
    but the Host header gives it away."""
    client, _ = bundle()
    response = client.get("/api/meta", headers={"Host": "evil.example.com"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "host_rejected"


def test_cross_site_origin_is_rejected_even_without_a_body(bundle):
    """CSRF: a body-less POST is a "simple request" that reaches write endpoints without
    preflight, so any site could pop the local folder picker (/api/workspaces/pick) or cancel
    runs (/api/runs/{id}/cancel); cross-origin requests always carry Origin, so off-allowlist
    origins are rejected.
    """
    client, _ = bundle()

    rejected = client.post(
        "/api/workspaces/pick", headers={"Origin": "https://evil.example.com"}
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "origin_rejected"

    # Loopback origins pass, as do requests without Origin (CLI); a nonexistent run probes the
    # boundary: 404 means it got past it rather than being stopped by 403.
    allowed = client.post(
        "/api/runs/run_nope/cancel",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert allowed.status_code == 404, allowed.text
    assert allowed.json()["error"]["code"] == "run_not_found"


def test_an_extra_host_can_be_allowed_explicitly(bundle, monkeypatch):
    """Non-loopback deployments can allow extra hosts through AVID_ALLOWED_HOSTS."""
    monkeypatch.setenv("AVID_ALLOWED_HOSTS", "avid.internal:8765")
    client, _ = bundle()

    response = client.get("/api/meta", headers={"Host": "avid.internal:8765"})

    assert response.status_code == 200


# ---------------- B10 ----------------


def test_session_list_does_not_replay_the_sessions(bundle, monkeypatch):
    """The list endpoint reads names and counts without opening each session (zero open() calls),
    while id, message_count, truncated_tail and active_run_id stay correct.
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
    assert entry["message_count"] >= 2  # user message + assistant message
    assert entry["truncated_tail"] is False
    assert entry["active_run_id"] is None


def test_unknown_api_is_json_404(bundle):
    client, _ = bundle()
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "not_found"

    # with no static build the root answers 503 static_missing, not 500
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


# ---------------- session CRUD ----------------


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
        in (USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT_MESSAGE)
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
    assert meta["event_types"] == list(EVENT_TYPES)
    assert meta["features"] == FEATURES
    assert meta["api_version"] == API_VERSION
    assert meta["capabilities"]["model"] == "test-model"
    assert "bash" in meta["capabilities"]["tools"]
    assert isinstance(meta["capabilities"]["skills"], list)
    assert meta["stream"]["heartbeat_seconds"] > 0

    # every capability the feature table declares must have a real endpoint
    assert client.get("/api/skills").status_code == 200
    if FEATURES["approvals"]:
        assert client.get("/api/runs/run_x/approvals").status_code == 404  # exists, run unknown
    if FEATURES["cancel"]:
        assert client.post("/api/runs/run_x/cancel").status_code == 404
    if FEATURES["deltas"]:
        # declared means usable: the route accepts ?deltas=1 (unknown run is still 404)
        assert client.get("/api/runs/run_x/events?deltas=1").status_code == 404
    else:
        assert "assistant_delta" in meta["event_types"]  # type defined, just not delivered
    if FEATURES["workspaces"]:
        # workspaces is endpoint-shaped: the route returns the list
        listed = client.get("/api/workspaces")
        assert listed.status_code == 200
        assert listed.json()["workspaces"]
    if FEATURES["danger_confirm"]:
        # danger confirmation is behavior-shaped with no new endpoint: declaring it means the
        # approvals route is really served (unknown run is 404).
        assert client.get("/api/runs/run_x/approvals").status_code == 404
    if FEATURES["full_access"]:
        # full access is parameter-shaped: the old permission field is rejected by
        # extra="forbid", and only full_access_ack grants it (wrong types rejected too).
        session = create_session(client).json()
        runs_url = f"/api/sessions/{session['id']}/runs"
        rejected = client.post(
            runs_url,
            json={"prompt": "x", "permission": "yolo"},
        )
        assert rejected.status_code == 422
        wrong_type = client.post(runs_url, json={"prompt": "x", "full_access_ack": 5})
        assert wrong_type.status_code == 422


def test_health(bundle):
    client, _ = bundle()
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["api_version"] == API_VERSION
    assert body["uptime_ms"] >= 0


# ---------------- branch endpoints ----------------


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

    # duplicate name 409 (rebuilding would silently drop that chain); bad fork point 400
    duplicate = client.post(f"/api/sessions/{session_id}/branches", json={"name": "b2"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "branch_exists"
    assert client.post(f"/api/sessions/{session_id}/branches", json={"at": "e_missing"}).status_code == 400

    # run on a branch: the body carries branch and main's chain stays untouched
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


# ---------------- wire format of injected reminders ----------------


def test_injected_reminder_keeps_its_notice_type_on_the_wire(bundle):
    """The frontend tells kernel-injected reminders from user speech by the entry type string:
    role is genuinely "user" and the nudge text is hook-chosen, so only the type distinguishes.
    """
    client, services = bundle()
    session_id = create_session(client).json()["id"]

    session = services.repo.open(services.runs.find_metadata(session_id))
    try:
        recorder = SessionRecorder(session)
        recorder.ensure_branch()
        recorder.on_message({"role": "user", "content": "问题"})
        recorder.on_message(
            {"role": "user", "content": "还有一步"}, entry_type=NOTICE_ENTRY
        )
    finally:
        session.close()

    entries = client.get(f"/api/sessions/{session_id}/entries?order=asc").json()["entries"]
    assert [entry["type"] for entry in entries] == ["message", "notice"]
    assert [entry["message"]["role"] for entry in entries] == ["user", "user"]


# ---------------- B16 and pagination ----------------


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

    # asc and desc are the same entries in opposite directions
    ascending = client.get(
        f"/api/sessions/{session_id}/entries", params={"order": "asc", "limit": 500}
    ).json()
    assert [item["seq"] for item in ascending["entries"]] == sorted(
        item["seq"] for item in ascending["entries"]
    )


def test_a_failed_run_leaves_a_durable_error_entry(bundle):
    """A failed run persists an error entry so the reason survives a refresh; it never enters the
    model context (the projection takes message/notice only).
    """
    from avid.providers.protocol import LLMError

    def failing(config, messages, **kwargs):
        raise LLMError("请求 https://api.example/v1/chat/completions 失败：The read operation timed out")

    client, _ = bundle(failing)
    session_id = create_session(client).json()["id"]
    started = client.post(
        f"/api/sessions/{session_id}/runs", json={"prompt": "跑一下", "auto_approve": True}
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    assert wait_for(
        lambda: client.get(f"/api/runs/{run_id}").json()["status"]
        in ("finished", "failed", "cancelled")
    )

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "llm_error"
    assert "timed out" in run["error"]["message"]

    entries = client.get(f"/api/sessions/{session_id}/entries?order=asc").json()["entries"]
    assert [entry["type"] for entry in entries] == ["message", "error"]
    assert entries[-1]["message"]["content"].startswith("运行失败：")

    # the persisted record replaces the vague interruption notice
    assert client.get(f"/api/sessions/{session_id}").json()["truncated_tail"] is False

    # the payload carries entry_id so the UI aligns the live row with the re-read session
    body = client.get(f"/api/runs/{run_id}/events").text
    finished = [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ") and '"run_failed"' in line
    ]
    assert finished and finished[0]["data"]["entry_id"] == entries[-1]["entry_id"]


def test_truncated_tail_covers_a_run_that_left_nothing(bundle):
    """A trailing user message counts as a truncated tail even when the round produced no reply
    at all (the shape left by a first model call that failed); a live run does not count.
    """
    client, services = bundle()
    session_id = create_session(client).json()["id"]
    session = services.repo.open(services.runs.find_metadata(session_id))
    try:
        SessionRecorder(session).ensure_branch().append_message({"role": "user", "content": "问题"})
    finally:
        session.close()

    assert client.get(f"/api/sessions/{session_id}").json()["truncated_tail"] is True

    # a run in progress does not count: no reply yet is expected there
    original = services.runs.active_run_id
    services.runs.active_run_id = lambda _session_id: "run_fake"  # type: ignore[method-assign]
    try:
        assert client.get(f"/api/sessions/{session_id}").json()["truncated_tail"] is False
    finally:
        services.runs.active_run_id = original  # type: ignore[method-assign]


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

    # the first frame's data is valid JSON with run_id / session_id / seq / type / data
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

    # explicit ?after= wins over the header
    explicit = client.get(
        f"/api/runs/{run_id}/events", params={"after": 0}, headers={"Last-Event-ID": "2"}
    )
    assert "id: 1\n" in explicit.text

    assert client.get("/api/runs/run_nope/events").status_code == 404


# ---------------- single point of tool-status classification ----------------


def test_classify_tool_status_is_single_point(bundle):
    assert classify_tool_status("ok") == "ok"
    assert classify_tool_status("错误：找不到文件") == "failed"
    assert (
        classify_tool_status("参数错误：offset 必须是 integer；请按工具 schema 修正后重试。")
        == "failed"
    )
    assert (
        classify_tool_status("工具执行失败：bash（boom）；不要用同样的参数重复调用。")
        == "failed"
    )
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


def test_child_tool_result_rides_the_wire_truncated():
    """A child run's tool result travels only this wire (not persisted, no durable channel);
    the parent's own results still report length only."""
    from avid.agent.events import TOOL_CALL_FINISHED, RunEvent
    from avid.web.schemas import SUBAGENT_CONTENT_CHARS, event_payload

    child = RunEvent(
        type=TOOL_CALL_FINISHED,
        data={
            "tool": "read_file",
            "tool_call_id": "k1",
            "content": "行" * (SUBAGENT_CONTENT_CHARS + 10),
            "subagent": {"task": "甲", "index": 0},
        },
        run_id="r1",
        ts=1,
    )
    payload = event_payload(child, "s1")["data"]

    assert len(payload["content"]) == SUBAGENT_CONTENT_CHARS
    assert payload["content_chars"] == SUBAGENT_CONTENT_CHARS + 10
    assert payload["status"] == "ok"

    parent = RunEvent(
        type=TOOL_CALL_FINISHED,
        data={"tool": "read_file", "tool_call_id": "k1", "content": "行"},
        run_id="r1",
        ts=1,
    )
    assert "content" not in event_payload(parent, "s1")["data"]


# ---------------- static files and SPA fallback ----------------


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
        # deep links fall back to the SPA shell (frontend routing takes over)
        deep = client.get("/settings")
        assert deep.status_code == 200 and "SPA" in deep.text

        # real static files come back with their own content type
        asset = client.get("/assets/app-abc123.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]

        # a missing asset with an extension is an explicit 404 (a browser must not get HTML)
        missing = client.get("/assets/stale-000.js")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "asset_not_found"
    finally:
        services.close()


# ---------------- usage snapshots ----------------

def test_branch_list_carries_persisted_usage(bundle):
    """Persisted usage comes back with the branch list, surviving switches and restarts."""
    client, _ = bundle(chat=ScriptedChat(make_turn("答")))
    session_id = create_session(client).json()["id"]

    # never run: null (the UI shows "-"), not a zero report
    fresh = client.get(f"/api/sessions/{session_id}/branches").json()["branches"][0]
    assert fresh["usage"] is None

    started = client.post(
        f"/api/sessions/{session_id}/runs", json={"prompt": "问题", "auto_approve": True}
    )
    run_id = started.json()["run_id"]
    assert wait_for(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished")

    run = client.get(f"/api/runs/{run_id}").json()
    # scripted model usage is prompt=1 / completion=2 / total=3 (support.make_turn).
    assert run["usage"]["context"]["tokens"] == 1
    assert run["usage"]["cache"]["hit_ratio"] is None
    # no AVID_CONTEXT_WINDOW and test-model is not in the built-in table: no guessed utilization
    assert run["usage"]["context"]["window"] is None
    assert run["usage"]["context"]["utilization"] is None

    branches = client.get(f"/api/sessions/{session_id}/branches").json()["branches"]
    main = next(item for item in branches if item["name"] == "main")
    assert main["usage"] == run["usage"]


def test_usage_is_per_branch_not_per_session(bundle):
    """Usage is per branch: switching shows that chain's own reading, a never-run branch is null."""
    client, _ = bundle(chat=ScriptedChat(make_turn("主线"), make_turn("分支上")))
    session_id = create_session(client).json()["id"]

    first = client.post(
        f"/api/sessions/{session_id}/runs",
        json={"prompt": "跑一下", "auto_approve": True, "branch": "main"},
    )
    assert wait_for(
        lambda: client.get(f"/api/runs/{first.json()['run_id']}").json()["status"]
        == "finished"
    )

    entries = client.get(f"/api/sessions/{session_id}/entries?order=asc").json()["entries"]
    fork = client.post(
        f"/api/sessions/{session_id}/branches", json={"at": entries[-1]["entry_id"]}
    ).json()
    assert fork["usage"] is None  # the new branch has not run yet

    branches = {
        item["name"]: item
        for item in client.get(f"/api/sessions/{session_id}/branches").json()["branches"]
    }
    assert set(branches) == {"main", fork["name"]}
    assert branches["main"]["usage"]["context"]["tokens"] == 1
    assert branches[fork["name"]]["usage"] is None
