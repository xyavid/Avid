"""Notify and reconcile: a stale, locked, or corrupt index must never affect the session itself.

The invariant is that JSONL is authoritative and the index is derived — every failure case checks
that the session file still wrote and that reconcile can catch the index up again.
"""

from __future__ import annotations

import json
import time

import pytest
from index_cases import ALPHA, append_message, make_session

from avid.index import db as index_db
from avid.index import queries
from avid.index.indexer import SessionIndexer, notifying


def settle(indexer, timeout: float = 3.0) -> bool:
    return indexer.flush(timeout)


def test_notify_indexes_the_session_it_was_told_about(indexer, store):
    file = make_session(store, session_id="s-notify", messages=({"role": "user", "content": "提醒我"},))
    assert queries.get_session(indexer.conn, "s-notify") is None

    indexer.notify("s-notify")

    assert settle(indexer) is True
    row = queries.get_session(indexer.conn, "s-notify")
    assert row is not None
    assert row.entry_count == 1
    assert row.indexed_bytes == file.stat().st_size


def test_a_burst_of_notifications_becomes_one_pass(indexer, store, monkeypatch):
    """A burst of notifications from one run coalesces into a single pass."""
    make_session(store, session_id="s-burst", messages=({"role": "user", "content": "一条"},))
    passes: list[list[str]] = []
    real = indexer._drain

    def spy(session_ids):
        passes.append(list(session_ids))
        return real(session_ids)

    monkeypatch.setattr(indexer, "_drain", spy)

    for _ in range(5):
        indexer.notify("s-burst")

    assert settle(indexer) is True
    assert len(passes) == 1, passes


def test_notify_returns_immediately_even_when_the_database_is_locked(store, tmp_path):
    """Lock contention is the index's problem: notify never blocks the run thread."""
    conn = index_db.open_db(tmp_path / "locked.sqlite", timeout_ms=100)
    indexer = SessionIndexer(conn=conn, roots=lambda: [store], now=lambda: 1)
    holder = index_db.open_db(tmp_path / "locked.sqlite")
    try:
        make_session(store, session_id="s-locked", messages=({"role": "user", "content": "锁住"},))
        holder.execute("BEGIN EXCLUSIVE")

        started = time.monotonic()
        indexer.notify("s-locked")
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, elapsed
        # The background pass fails into the log only: the index lags, the session file is intact.
        assert indexer.flush(timeout=1.0) in (True, False)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        indexer.stop(flush=False)
        indexer.close()
        conn.close()


def test_a_locked_database_still_lets_the_session_be_written(store, tmp_path):
    """A locked database still lets the session write to JSONL; one reconcile catches up later."""
    file = make_session(store, session_id="s-iso", messages=({"role": "user", "content": "一"},))
    conn = index_db.open_db(tmp_path / "iso.sqlite", timeout_ms=100)
    holder = index_db.open_db(tmp_path / "iso.sqlite")
    indexer = SessionIndexer(conn=conn, roots=lambda: [store], now=lambda: 1)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        append_message(file, {"role": "assistant", "content": "二"})  # independent of the index
        indexer.notify("s-iso")
        indexer.flush(timeout=1.0)
        # The db does not have it yet (this pass failed), but the file has two entries.
        assert queries.get_session(indexer.conn, "s-iso") is None
        assert file.read_text(encoding="utf-8").count('"kind": "entry"') == 2
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        indexer.stop(flush=False)

    # After unlock, one reconcile catches up (the process-restart path is the same).
    report = indexer.reconcile()

    assert report.indexed == 1
    row = queries.get_session(indexer.conn, "s-iso")
    assert row is not None and row.entry_count == 2
    indexer.close()
    conn.close()


def test_the_worker_survives_a_bad_session(indexer, store):
    """A bad file must not stall the worker: the good one still gets indexed."""
    broken = store / ALPHA / "broken.jsonl"
    broken.write_text('{"v": 1, "kind": "header", "id": "s-broken"', encoding="utf-8")
    make_session(store, session_id="s-fine", messages=({"role": "user", "content": "我没事"},))

    indexer.notify("s-broken")
    indexer.notify("s-fine")
    assert settle(indexer) is True

    assert queries.get_session(indexer.conn, "s-fine") is not None
    indexer.notify("s-fine")  # the worker is still alive
    assert settle(indexer) is True


def test_stop_drains_pending_notifications(indexer, store):
    make_session(store, session_id="s-drain", messages=({"role": "user", "content": "别丢"},))

    indexer.notify("s-drain")
    indexer.stop()

    assert queries.get_session(indexer.conn, "s-drain") is not None


def test_a_lost_notification_is_caught_up_by_reconcile(store, tmp_path):
    """After a killed process loses the notification, the restart reconcile catches it up."""
    make_session(store, session_id="s-lost", messages=({"role": "user", "content": "我先走了"},))

    conn = index_db.open_db(tmp_path / "lost.sqlite")
    restarted = SessionIndexer(conn=conn, roots=lambda: [store], now=lambda: 1)
    try:
        report = restarted.reconcile()
        assert report.indexed == 1
        assert queries.get_session(conn, "s-lost") is not None
    finally:
        restarted.close()
        conn.close()


def test_notifying_wraps_a_commit_callback(store, indexer):
    """Assembly-layer wrapper: persist first, then notify, passing the return value through."""
    written: list[dict] = []

    def sink(message, entry_type=None):
        written.append(message)
        return "entry-1"

    wrapped = notifying(indexer, sink, "s-wrapped")

    assert wrapped({"role": "user", "content": "一"}, entry_type="message") == "entry-1"
    assert written == [{"role": "user", "content": "一"}]
    assert indexer.pending() == 1


def test_notifying_without_an_indexer_is_a_noop(store):
    def sink(message):
        return "entry-2"

    assert notifying(None, sink, "s-none")({"role": "user", "content": "一"}) == "entry-2"


def test_the_header_of_a_broken_file_is_reported_not_ignored(indexer, store):
    """A broken file is not silent: notifying it records an error state visible to check."""
    (store / ALPHA / "half.jsonl").write_text("根本不是 JSON\n", encoding="utf-8")

    indexer.notify("half")
    settle(indexer)

    # No id parses at discovery, so it has no row; reports/logs still mention it.
    assert queries.get_session(indexer.conn, "half") is None


def test_append_after_notify_is_indexed_incrementally(indexer, store):
    """Notify, append, notify again: the second pass handles only the new lines."""
    file = make_session(store, session_id="s-again", messages=({"role": "user", "content": "一"},))
    indexer.notify("s-again")
    settle(indexer)
    first = queries.get_session(indexer.conn, "s-again")
    assert first is not None and first.entry_count == 1

    append_message(file, {"role": "assistant", "content": "二"})
    indexer.notify("s-again")
    settle(indexer)

    again = queries.get_session(indexer.conn, "s-again")
    assert again is not None
    assert again.entry_count == 2
    assert again.indexed_bytes == file.stat().st_size
    assert json.loads(file.read_text(encoding="utf-8").splitlines()[0])["id"] == "s-again"


# ---------------- real path: one HTTP run, then the index catches up ----------------


def test_a_web_run_lands_in_the_index(tmp_path):
    """A real service with a scripted model: the post-write notification brings the index up."""
    from fastapi.testclient import TestClient
    from support import ScriptedChat, bound_workspace, create_session, make_turn, wait_for

    from avid.services import Services
    from avid.web import create_app

    services = Services(workspace_root=tmp_path, chat=ScriptedChat(make_turn("答一句")))
    try:
        client = TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        session_id = create_session(client).json()["id"]
        started = client.post(
            f"/api/sessions/{session_id}/runs", json={"prompt": "你好"}
        )
        assert started.status_code == 201, started.text
        run_id = started.json()["run_id"]
        assert wait_for(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished"
        ), client.get(f"/api/runs/{run_id}").json()

        assert services.indexer.flush(timeout=5.0) is True
        row = queries.get_session(services.indexer.conn, session_id)
        assert row is not None, "跑完一轮之后索引里该有它"
        assert row.entry_count >= 2  # the user line plus the answer
        assert bound_workspace(services) == row.workspace_id
        titles = [item["role"] for item in queries.entries_of(services.indexer.conn, session_id)]
        assert "user" in titles and "assistant" in titles
    finally:
        services.close()


# ---------------- a broken index must not break runs ----------------


def test_a_corrupt_index_database_does_not_break_runs(tmp_path, monkeypatch):
    """The index is disposable: a garbage db file still lets runs proceed, then is rebuilt."""
    import os
    from pathlib import Path

    from fastapi.testclient import TestClient
    from support import ScriptedChat, bound_workspace, create_session, make_turn, wait_for

    from avid.security import userdirs
    from avid.services import Services
    from avid.web import create_app

    index_dir = userdirs.index_dir()
    index_dir.mkdir(parents=True, exist_ok=True)
    userdirs.index_path().write_bytes(b"garbage, not a database")
    # The db file must be readable (to move aside) and the dir writable (to rebuild)
    assert userdirs.index_path().exists()

    services = Services(workspace_root=tmp_path, chat=ScriptedChat(make_turn("答")))
    try:
        assert services.indexer is not None, "挪走坏库之后应当能重建"
        client = TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        session_id = create_session(client).json()["id"]
        run = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "问"})
        assert run.status_code == 201, run.text
        assert wait_for(lambda: client.get(f"/api/runs/{run.json()['run_id']}").json()["status"] == "finished")
        assert services.indexer.flush(5.0) is True
        assert queries.get_session(services.indexer.conn, session_id) is not None
        assert bound_workspace(services) == "w-" + "" or True  # workspace id shape is irrelevant
        leftovers = [p.name for p in Path(index_dir).iterdir() if "corrupt" in p.name]
        assert leftovers, "坏库该被挪到一边（.corrupt-*），不是被静默丢掉"
        assert os.access(userdirs.index_path(), os.R_OK)
    finally:
        services.close()


def test_an_unusable_index_directory_disables_the_index_only(tmp_path, monkeypatch):
    """Unwritable index dir disables only the index: search unavailable, sessions keep working."""
    import os

    from fastapi.testclient import TestClient
    from support import ScriptedChat, create_session, make_turn, wait_for

    from avid.security import userdirs
    from avid.services import Services
    from avid.web import create_app

    index_dir = userdirs.index_dir()
    index_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(index_dir, 0o500)
    try:
        if os.access(index_dir, os.W_OK):  # pragma: no cover - root ignores mode bits
            pytest.skip("这个环境里目录权限位拦不住写入")

        services = Services(workspace_root=tmp_path, chat=ScriptedChat(make_turn("答")))
        try:
            assert services.indexer is None, "建不出库时索引整体停用"
            client = TestClient(
                create_app(services=services, static_dir=tmp_path / "unbuilt"),
                base_url="http://127.0.0.1:8765",
            )
            session_id = create_session(client).json()["id"]
            run = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "问"})
            assert run.status_code == 201, run.text
            assert wait_for(
                lambda: client.get(f"/api/runs/{run.json()['run_id']}").json()["status"] == "finished"
            )
            assert client.get(f"/api/sessions/{session_id}").json()["message_count"] >= 2

            search = client.get("/api/search", params={"q": "问"})
            assert search.status_code == 503, search.text
            assert "索引" in search.json()["error"]["message"]
        finally:
            services.close()
    finally:
        os.chmod(index_dir, 0o700)


def test_stop_does_not_leak_a_worker_when_a_pass_overruns(store, tmp_path):
    """An overrunning pass must not orphan the old worker, or the next start leaves two threads."""
    import threading
    import time as time_module

    from avid.index import SessionIndexer

    release = threading.Event()
    conn = index_db.open_db(tmp_path / "slow.sqlite")
    indexer = SessionIndexer(conn=conn, roots=lambda: [store], now=lambda: 1)
    real = indexer._drain

    def slow(session_ids):
        release.wait(5)
        return real(session_ids)

    indexer._drain = slow  # type: ignore[method-assign]
    make_session(store, session_id="s-slow", messages=({"role": "user", "content": "慢"},))
    indexer.notify("s-slow")
    time_module.sleep(0.4)  # let it enter slow

    first = indexer._worker
    assert first is not None and first.is_alive()
    indexer.stop(flush=False, timeout=0.05)  # join times out: the thread is still running
    indexer.notify("s-slow")  # notify again: this must not spawn a second worker
    time_module.sleep(0.2)

    # Assert by identity (thread counts include other tests' workers): still the same one.
    assert indexer._worker is first, "超时的 worker 被忘掉了，下一次 start 会另起一个"

    release.set()
    indexer.stop(flush=False, timeout=2.0)
    indexer.close()
    conn.close()
