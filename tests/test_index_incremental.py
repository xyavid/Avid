"""索引器：发现、元数据、条目定位与增量游标。

会话文件用真仓库写（JsonlSessionRepo + SessionRecorder），所以索引面对的就是真实格式；
断言落在「读回原文」这条性质上，而不只是行数对不对。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avid.index import db as index_db
from avid.index import queries, scanner
from avid.index.indexer import SessionIndexer
from avid.session import JsonlSessionRepo, SessionRecorder
from avid.session.values import session_name

ALPHA, BETA = "w-alpha", "w-beta"
WORKSPACES = {ALPHA: ("/ws/alpha", "alpha"), BETA: ("/ws/beta", "beta")}


@pytest.fixture
def store(tmp_path) -> Path:
    root = tmp_path / "sessions"
    for workspace in WORKSPACES:
        (root / workspace).mkdir(parents=True)
    return root


@pytest.fixture
def indexer(store, tmp_path):
    conn = index_db.open_db(tmp_path / "index.sqlite")
    instance = SessionIndexer(
        conn=conn,
        roots=lambda: [store],
        lookup_workspace=lambda wid: WORKSPACES.get(wid),
        now=lambda: 1_700_000_000_000,
    )
    try:
        yield instance
    finally:
        instance.close()
        conn.close()


def make_session(
    store: Path,
    *,
    workspace: str | None = ALPHA,
    session_id: str | None = None,
    messages: tuple[dict, ...] = (),
    name: str | None = None,
) -> Path:
    """Write a real session under the store; returns its file."""
    directory = store / (workspace or "unowned")
    repo = JsonlSessionRepo(directory, workspace=workspace)
    try:
        session = repo.create(id=session_id)
        recorder = SessionRecorder(session)
        for message in messages:
            recorder.on_message(message)
        if name is not None:
            session.set_value(session_name(), name)
        glob = f"*_{session.metadata.id}.jsonl"
    finally:
        repo.close()
    return next(directory.glob(glob))


def append_message(path: Path, message: dict, *, workspace: str | None = ALPHA) -> None:
    """Append one more committed entry to an existing session file."""
    repo = JsonlSessionRepo(path.parent, workspace=workspace)
    try:
        session = repo.open(next(item for item in repo.list() if item.id in path.name))
        SessionRecorder(session, branch="main").on_message(message)
    finally:
        repo.close()


def read_byte_range(path: Path, offset: int, length: int) -> dict:
    """The jump the index promises: byte range → the original record, parsed by the store's own codec."""
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(length).decode("utf-8")
    payload = json.loads(raw.strip())
    return payload if isinstance(payload, dict) else payload[0]


# ---------------- P2：会话元数据


def test_index_all_records_session_metadata(indexer, store):
    file = make_session(
        store,
        session_id="s-alpha-1",
        messages=(
            {"role": "user", "content": "先看看 pyproject.toml"},
            {"role": "assistant", "content": "项目名是 Avid"},
        ),
        name="看项目名",
    )

    report = indexer.index_all()

    assert (report.indexed, report.failed) == (1, 0)
    row = queries.get_session(indexer.conn, "s-alpha-1")
    assert row is not None
    assert row.file_path == str(file)
    assert row.workspace_id == ALPHA
    assert row.workspace_root == "/ws/alpha"
    assert row.workspace_name == "alpha"
    assert row.title == "看项目名"
    assert row.first_user_text == "先看看 pyproject.toml"
    assert row.entry_count == 2
    assert row.index_status == "ok"
    assert row.indexed_bytes == row.file_size
    assert row.created_at is not None and row.updated_at is not None


def test_workspace_falls_back_to_the_directory_name(indexer, store):
    """header 里没有 workspaceId 的老文件，按它所在的目录归属。"""
    make_session(store, workspace=ALPHA, session_id="s-nohdr")

    # 抹掉 header 里的 workspaceId，模拟阶段 56 之前写下的文件。
    file = next((store / ALPHA).glob("*_s-nohdr.jsonl"))
    lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
    header = json.loads(lines[0])
    header.pop("workspaceId")
    file.write_text(json.dumps(header, ensure_ascii=False) + "\n" + "".join(lines[1:]), encoding="utf-8")

    indexer.index_all()

    row = queries.get_session(indexer.conn, "s-nohdr")
    assert row is not None and row.workspace_id == ALPHA


def test_list_sessions_orders_by_updated_and_filters_by_workspace(indexer, store):
    older = make_session(store, workspace=ALPHA, session_id="s-a", messages=({"role": "user", "content": "一"},))
    make_session(store, workspace=BETA, session_id="s-b", messages=({"role": "user", "content": "二"},))
    indexer.index_all()

    # 把 alpha 那个文件改新一点（mtime 就是 updated_at 的来源）。
    import os

    stat = older.stat()
    os.utime(older, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))

    indexer.reconcile()  # 再跑一遍：这一遍才会把新的 mtime 记成 updated_at

    everything = queries.list_sessions(indexer.conn)
    assert [row.session_id for row in everything] == ["s-a", "s-b"]
    only_beta = queries.list_sessions(indexer.conn, workspace_id=BETA)
    assert [row.session_id for row in only_beta] == ["s-b"]
    assert queries.sessions_by_workspace(indexer.conn) == {ALPHA: 1, BETA: 1}


def test_the_index_survives_a_restart(indexer, store, tmp_path):
    make_session(store, session_id="s-restart", messages=({"role": "user", "content": "还在吗"},))
    indexer.index_all()
    indexer.conn.close()

    again = index_db.open_db(tmp_path / "index.sqlite")
    try:
        row = queries.get_session(again, "s-restart")
        assert row is not None and row.entry_count == 1
        assert queries.count_sessions(again) == 1
    finally:
        again.close()


def test_a_file_that_is_not_a_session_is_skipped_not_fatal(indexer, store):
    (store / ALPHA / "notes.jsonl").write_text("这不是会话文件\n", encoding="utf-8")
    make_session(store, session_id="s-good", messages=({"role": "user", "content": "好"},))

    report = indexer.index_all()

    assert report.indexed == 1
    assert any("notes.jsonl" in detail for detail in report.details)
    assert queries.get_session(indexer.conn, "s-good") is not None


def test_an_unknown_storage_version_is_not_guessed(indexer, store):
    file = make_session(store, session_id="s-future", messages=({"role": "user", "content": "未来"},))
    lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
    header = json.loads(lines[0])
    header["storageVersion"] = 99
    file.write_text(json.dumps(header) + "\n" + "".join(lines[1:]), encoding="utf-8")

    indexer.index_all()

    row = queries.get_session(indexer.conn, "s-future")
    assert row is not None and row.index_status == "unsupported"
    assert "storageVersion=99" in (row.last_error or "")


# ---------------- P3：条目定位与增量游标


def test_entries_land_with_line_offsets_that_read_back(indexer, store):
    file = make_session(
        store,
        session_id="s-locate",
        messages=(
            {"role": "user", "content": "第一个问题"},
            {"role": "assistant", "content": "第一个回答"},
        ),
    )
    indexer.index_all()

    rows = queries.entries_of(indexer.conn, "s-locate")
    assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows)
    assert [row["role"] for row in rows] == ["user", "assistant"]

    for row in rows:
        record = read_byte_range(file, int(row["byte_offset"]), int(row["byte_length"]))
        assert record["id"] == row["entry_id"]
        assert record["seq"] == row["seq"]
    assert "\n" not in json.dumps(record)


def test_the_second_pass_starts_at_the_cursor(indexer, store, monkeypatch):
    file = make_session(store, session_id="s-incremental", messages=({"role": "user", "content": "一"},))
    indexer.index_all()
    first_size = file.stat().st_size

    offsets: list[int] = []
    real = scanner.scan_file

    def spy(path, *, start_offset=0, previous=None):
        offsets.append(start_offset)
        return real(path, start_offset=start_offset, previous=previous)

    monkeypatch.setattr("avid.index.indexer.scan_file", spy)
    append_message(file, {"role": "assistant", "content": "二"})
    indexer.reconcile()

    assert offsets == [first_size], offsets
    row = queries.get_session(indexer.conn, "s-incremental")
    assert row is not None and row.entry_count == 2
    assert row.indexed_bytes == file.stat().st_size


def test_a_torn_tail_waits_for_the_rest_of_the_line(indexer, store):
    file = make_session(store, session_id="s-torn", messages=({"role": "user", "content": "完整"},))
    indexer.index_all()
    complete = file.stat().st_size

    with file.open("ab") as handle:
        handle.write(b'{"kind": "entry", "seq": 2, "timestamp": 1, "id": "half')

    indexer.reconcile()
    row = queries.get_session(indexer.conn, "s-torn")
    assert row is not None
    assert row.entry_count == 1  # 半条不算数
    assert row.indexed_bytes == complete  # 游标停在残片之前

    with file.open("ab") as handle:  # 残片补齐（换行收尾）
        handle.write(
            '", "type": "message", "message": {"role": "assistant", "content": "补上了"}}\n'.encode()
        )

    indexer.reconcile()
    row = queries.get_session(indexer.conn, "s-torn")
    assert row is not None and row.entry_count == 2
    assert row.indexed_bytes == file.stat().st_size


def test_a_shortened_file_is_rebuilt_from_scratch(indexer, store):
    """文件变短 = 被截断或被换掉：游标越界，整篇重扫（不猜缺了什么）。"""
    file = make_session(
        store,
        session_id="s-short",
        messages=(
            {"role": "user", "content": "一"},
            {"role": "assistant", "content": "二"},
            {"role": "user", "content": "三"},
        ),
    )
    indexer.index_all()
    assert queries.get_session(indexer.conn, "s-short").entry_count == 3

    lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
    file.write_text("".join(lines[:3]), encoding="utf-8")  # header + 两次提交

    indexer.reconcile()

    row = queries.get_session(indexer.conn, "s-short")
    assert row is not None
    assert row.entry_count == 2
    assert row.indexed_bytes == file.stat().st_size
    assert row.index_status == "ok"
    # 一次消息提交写一行 = [entry, tip 值]（两个写共用一行的字节区间），
    # 所以条目 seq 是隔一个的：1、3、5…；截掉最后一行正好留下前两条。
    assert [item["seq"] for item in queries.entries_of(indexer.conn, "s-short")] == [1, 3]


def test_a_deleted_file_is_forgotten(indexer, store):
    file = make_session(store, session_id="s-gone", messages=({"role": "user", "content": "删我"},))
    make_session(store, session_id="s-keep", messages=({"role": "user", "content": "留我"},))
    indexer.index_all()

    file.unlink()
    report = indexer.reconcile()

    assert report.removed == 1
    assert queries.get_session(indexer.conn, "s-gone") is None
    assert queries.entries_of(indexer.conn, "s-gone") == []  # 级联删掉了
    assert queries.get_session(indexer.conn, "s-keep") is not None


def test_indexing_twice_does_not_duplicate_rows(indexer, store):
    make_session(store, session_id="s-twice", messages=({"role": "user", "content": "一"},))
    indexer.index_all()
    indexer.index_all()

    row = queries.get_session(indexer.conn, "s-twice")
    assert row is not None and row.entry_count == 1
    assert len(queries.entries_of(indexer.conn, "s-twice")) == 1


def test_rebuild_replaces_instead_of_appending(indexer, store):
    make_session(
        store,
        session_id="s-rebuild",
        messages=({"role": "user", "content": "一"}, {"role": "assistant", "content": "二"}),
    )
    indexer.index_all()

    report = indexer.rebuild("s-rebuild")

    assert report.indexed == 1
    assert len(queries.entries_of(indexer.conn, "s-rebuild")) == 2
    row = queries.get_session(indexer.conn, "s-rebuild")
    assert row is not None and row.entry_count == 2


def test_rebuild_by_id_can_discover_a_session_the_index_never_saw(indexer, store):
    make_session(store, session_id="s-late", messages=({"role": "user", "content": "晚到"},))

    report = indexer.rebuild("s-late")

    assert report.indexed == 1
    assert queries.get_session(indexer.conn, "s-late") is not None


def test_entries_report_their_own_location(indexer, store):
    file = make_session(store, session_id="s-point", messages=({"role": "user", "content": "定位我"},))
    indexer.index_all()
    entry_id = queries.entries_of(indexer.conn, "s-point")[0]["entry_id"]

    found = queries.entry_location(indexer.conn, "s-point", str(entry_id))

    assert found is not None
    assert found["file_path"] == str(file)
    assert read_byte_range(file, int(found["byte_offset"]), int(found["byte_length"]))["id"] == entry_id
