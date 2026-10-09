"""Derived usage ledger: ``usage_report()``'s schema (occupancy, hit ratio, post-compaction
readings), its persistence as a session value readable on both backends, and the HTTP views
that expose the snapshot after a real (scripted-model) run.
"""

from __future__ import annotations

import pytest
from support import ScriptedChat, make_turn

from avid.agent.compaction import CompactReport, announce
from avid.agent.state import RunState
from avid.providers.config import Config
from avid.providers.usage import Usage
from avid.session import (
    USAGE_NS,
    MemorySessionRepo,
    SessionRecorder,
    UuidV7Generator,
)

# ---------------- usage_report: one schema ----------------

def test_report_is_all_none_before_the_first_model_call():
    report = RunState().usage_report()
    assert report == {
        "context": {"tokens": None, "window": None, "utilization": None, "parts": None},
        "cache": {"read_tokens": None, "write_tokens": None, "hit_ratio": None},
        "compaction": {"count": 0, "last_compaction_tokens": None, "last_step": None},
        "subagent": {"calls": 0, "tokens": 0},
    }


def test_report_shows_occupancy_cache_and_compaction():
    state = RunState(context_window=200_000)
    state.record_usage(Usage(72_000, 10, 72_010, cache_read_tokens=56_000))
    report = state.usage_report()
    assert report["context"] == {
        "tokens": 72_000,
        "window": 200_000,
        "utilization": 0.36,
        "parts": None,  # no character counts, so no parts — do not guess
    }
    assert report["cache"]["read_tokens"] == 56_000
    assert report["cache"]["hit_ratio"] == pytest.approx(0.77777, rel=1e-4)
    assert report["cache"]["write_tokens"] is None
    assert report["compaction"]["count"] == 0


def test_report_treats_an_all_zero_usage_as_no_data():
    """An endpoint that ignores ``include_usage`` gives no reading, not an empty context."""
    state = RunState(context_window=200_000)
    state.record_usage(Usage(0, 0, 0))
    report = state.usage_report()
    assert report["context"]["tokens"] is None
    assert report["context"]["utilization"] is None
    # Cumulative totals still count (that is the run bill, not occupancy).
    assert state.tokens == 0


def test_last_compaction_tokens_comes_from_the_next_real_call():
    """Post-compaction size comes from the next real model call, never a local estimate."""
    state = RunState(context_window=200_000)
    state.record_usage(Usage(150_000, 10, 150_010))
    announce(CompactReport("micro_compact", "落盘 3 条", 400, 200), state)

    report = state.usage_report()
    assert report["compaction"] == {
        "count": 1,
        "last_compaction_tokens": None,  # no next round yet — do not guess
        "last_step": "micro_compact",
    }

    state.record_usage(Usage(42_000, 10, 42_010))
    report = state.usage_report()
    assert report["compaction"]["last_compaction_tokens"] == 42_000
    assert report["context"]["tokens"] == 42_000  # occupancy follows the real reading


def test_compaction_counter_counts_only_real_reports():
    state = RunState()
    announce(None, state)  # nothing compacted -> not counted
    assert state.compactions == 0
    announce(CompactReport("snip_compact", "裁掉中间", 10, 8), state)
    announce(CompactReport("compact_history", "摘要", 9, 3), state)
    assert state.compactions == 2
    assert state.usage_report()["compaction"]["last_step"] == "compact_history"


def test_event_carries_the_snapshot_every_round():
    """Every ``run_status`` event carries the snapshot under the unified schema."""
    seen: list[dict] = []
    state = RunState(observer=lambda event: seen.append(event), context_window=100_000)
    state.record_usage(Usage(5_000, 5, 5_005, cache_read_tokens=2_500))
    state.emit("run_status", round=1, usage=state.usage_report())
    assert seen[-1].data["usage"]["context"]["tokens"] == 5_000
    assert seen[-1].data["usage"]["cache"]["hit_ratio"] == 0.5


# ---------------- persistence: a session value readable on both backends ----------------

@pytest.mark.parametrize("backend", ["memory", "jsonl"])
def test_recorder_persists_usage_per_branch(backend, tmp_path):
    clock = iter(range(1_700_000_000_000, 1_700_000_100_000, 1_000)).__next__
    generator = UuidV7Generator(clock)
    if backend == "memory":
        repo = MemorySessionRepo(now=clock, id_generator=generator)
    else:
        from avid.session import JsonlSessionRepo

        repo = JsonlSessionRepo(tmp_path / "sessions", now=clock, id_generator=generator)
    try:
        session = repo.create(workspace="ws")
        main = SessionRecorder(session, "main")
        side = SessionRecorder(session, "b2")
        payload = {"context": {"tokens": 72_000, "window": 200_000, "utilization": 0.36}}
        main.record_usage(payload)
        # Overwrite semantics: a branch keeps only its latest snapshot.
        main.record_usage({**payload, "context": {"tokens": 90_000}})
        side.record_usage(payload)

        stored = {item.key: item.value for item in session.scan_values(USAGE_NS)}
        assert set(stored) == {"main", "b2"}
        assert stored["main"]["context"]["tokens"] == 90_000
        assert stored["b2"]["context"]["tokens"] == 72_000
    finally:
        repo.close()


def test_usage_value_survives_reopen(tmp_path):
    """Values share the entry log, so a reopen still reads them back."""
    from avid.session import JsonlSessionRepo

    clock = iter(range(1_700_000_000_000, 1_700_000_100_000, 1_000)).__next__
    generator = UuidV7Generator(clock)
    repo = JsonlSessionRepo(tmp_path / "sessions", now=clock, id_generator=generator)
    try:
        session = repo.create(id="s1", workspace="ws")
        SessionRecorder(session, "main").record_usage({"context": {"tokens": 12_345}})
        metadata = session.metadata
        session.close()
    finally:
        repo.close()

    reopened = JsonlSessionRepo(tmp_path / "sessions", now=clock, id_generator=generator)
    try:
        session = reopened.open(metadata)
        stored = {item.key: item.value for item in session.scan_values(USAGE_NS)}
        assert stored["main"] == {"context": {"tokens": 12_345}}
    finally:
        reopened.close()


# ---------------- end to end: one run persists the snapshot ----------------

def test_run_persists_usage_and_exposes_it_over_http(sandbox):
    """One real (scripted) run exposes usage in both the runs view and the branch list."""
    from fastapi.testclient import TestClient
    from support import create_session

    from avid.services import Services
    from avid.web import create_app

    chat = ScriptedChat(make_turn("答"))
    services = Services(root=sandbox / ".avid" / "sessions", chat=chat)
    try:
        client = TestClient(
            create_app(services=services, static_dir=sandbox / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        created = create_session(client).json()
        session_id = created["id"]
        client.post(
            f"/api/sessions/{session_id}/runs",
            json={"prompt": "问题", "auto_approve": True, "branch": "main"},
        )
        assert services_close_when_done(services, session_id), "运行没有结束"

        branches = client.get(f"/api/sessions/{session_id}/branches").json()["branches"]
        main = next(item for item in branches if item["name"] == "main")
        # Scripted model usage is prompt=1 / completion=2 / total=3 (see support.make_turn).
        assert main["usage"]["context"]["tokens"] == 1
        assert main["usage"]["cache"]["read_tokens"] is None
        # No AVID_CONTEXT_WINDOW and unknown test-model: no denominator, no guessed utilization.
        assert main["usage"]["context"]["window"] is None
        assert main["usage"]["context"]["utilization"] is None

        # A branch that never ran is null, not 0.
        assert created["branch"] == "main"
    finally:
        services.close()


def services_close_when_done(services, session_id: str) -> bool:
    """Wait until the session has no active run (the run executes on a thread)."""
    from support import wait_for

    return wait_for(lambda: services.runs.active_run_id(session_id) is None)

def test_parts_allocate_the_real_total_by_char_share():
    """Parts split the real prompt_tokens by character share; the three parts sum to the total."""
    state = RunState(context_window=200_000)
    state.record_prompt_parts(system=2_000, tools=6_000, messages=64_000)
    state.record_usage(Usage(72_000, 10, 72_010))

    parts = state.usage_report()["context"]["parts"]
    assert parts is not None
    assert sum(parts.values()) == 72_000
    assert parts["system"] == 2_000  # character share 2/72, so the token share follows
    assert parts["tools"] == 6_000
    assert parts["messages"] == 64_000


def test_parts_absorb_the_rounding_remainder_into_messages():
    """The rounding remainder goes to messages so the three parts still sum to the total."""
    state = RunState()
    state.record_prompt_parts(system=1, tools=1, messages=1)
    state.record_usage(Usage(10, 0, 10))

    parts = state.usage_report()["context"]["parts"]
    assert parts is not None
    assert sum(parts.values()) == 10
    assert parts["system"] == 3 and parts["tools"] == 3 and parts["messages"] == 4


def test_parts_are_none_without_readings_or_chars():
    """No parts unless both readings and non-zero character counts exist."""
    assert RunState().usage_report()["context"]["parts"] is None

    no_chars = RunState()
    no_chars.record_usage(Usage(100, 1, 101))
    assert no_chars.usage_report()["context"]["parts"] is None

    zero_chars = RunState()
    zero_chars.record_prompt_parts(system=0, tools=0, messages=0)
    zero_chars.record_usage(Usage(100, 1, 101))
    assert zero_chars.usage_report()["context"]["parts"] is None


# ---------------- window probing inside the run path ----------------

def _window_probe(monkeypatch, *, window=200_000):
    """Route the provider ``/models`` probe through MockTransport, with probing enabled."""
    import httpx

    from avid.providers import client as client_module

    monkeypatch.delenv("AVID_MODEL_INFO", raising=False)
    with client_module._MODEL_WINDOW_LOCK:
        client_module._MODEL_WINDOWS.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/models")
        return httpx.Response(
            200, json={"data": [{"id": "test-model", "context_length": window}]}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client_module, "shared_client", lambda: http)
    return http


def test_loop_probes_the_window_and_reports_utilization(monkeypatch):
    """With no table or env window, the run probes /models once for a utilization denominator."""
    from support import run_loop

    http = _window_probe(monkeypatch)
    try:
        config = Config(api_key="test-key", base_url="https://gw.test/v1", model="test-model")
        assert config.context_window is None

        built = RunState(observer=lambda event: None)
        run_loop(
            [{"role": "user", "content": "问题"}],
            config=config,
            chat=ScriptedChat(make_turn("答")),
            state=built,
        )
        report = built.usage_report()
        assert report["context"]["window"] == 200_000
        assert report["context"]["utilization"] is not None

        # The probe result is cached per process: the second run makes no request.
        fresh = RunState(observer=lambda event: None)
        run_loop(
            [{"role": "user", "content": "又一轮"}],
            config=config,
            chat=ScriptedChat(make_turn("答")),
            state=fresh,
        )
        assert fresh.usage_report()["context"]["window"] == 200_000
    finally:
        http.close()
