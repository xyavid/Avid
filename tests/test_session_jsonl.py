"""File backend: format, replay, torn lines, corrupt lines and directory scanning.

These rules only surface on disk (the memory backend hides them); cross-backend consistency
lives in test_session_conformance.py.
"""

from __future__ import annotations

import itertools
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from avid.session import (
    BranchScan,
    EntryQuery,
    JsonlSessionMetadata,
    JsonlSessionRepo,
    SessionClosedError,
    SessionExistsError,
    SessionInvalidMessageError,
    SessionInvariantError,
    SessionLockedError,
    SessionStorageError,
    UuidV7Generator,
)
from avid.session.jsonl import (
    FORMAT_VERSION,
    JsonlHeader,
    JsonlStorage,
    encode_header,
    encode_transaction,
    parse_header,
    session_file_name,
    summarize_file,
)
from avid.session.types import (
    CommittedEntry,
    CommittedValueSet,
    EntryWrite,
    NewEntry,
)

USER = {"role": "user", "content": "一"}
ASSISTANT = {"role": "assistant", "content": "二"}


def clock(start: int = 1_700_000_000_000, step: int = 1_000):
    counter = itertools.count(start, step)
    return lambda: next(counter)


def make_repo(root: Path) -> JsonlSessionRepo:
    tick = clock()
    return JsonlSessionRepo(root, now=tick, id_generator=UuidV7Generator(tick))


def only_file(root: Path) -> Path:
    files = sorted(root.glob("*.jsonl"))
    assert len(files) == 1, files
    return files[0]


def write_lines(path: Path, *lines: str) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def entry_line(seq: int, entry_id: str, parent: str | None, timestamp: int = 1) -> str:
    return encode_transaction(
        [CommittedEntry(seq, timestamp, NewEntry(id=entry_id, parent_id=parent, message=USER))]
    )


def _message_line(
    seq: int, entry_id: str, parent: str | None, message: dict
) -> str:
    return encode_transaction(
        [
            CommittedEntry(
                seq, 1, NewEntry(id=entry_id, parent_id=parent, message=message)
            )
        ]
    )


# ---------------- format ----------------


def test_create_writes_only_a_header(tmp_path):
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    path = only_file(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    header = json.loads(lines[0])
    assert header == {
        "v": FORMAT_VERSION,
        "kind": "header",
        "id": "demo",
        "storageVersion": 1,
        "createdAt": session.metadata.created_at,
    }
    assert list(tmp_path.glob("*.tmp")) == []
    session.close()


def test_every_commit_is_exactly_one_appended_line(tmp_path):
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    session.create_branch("main", None)
    session.branch("main").append_message(USER)
    session.set_name("名字")
    lines = only_file(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert json.loads(lines[0])["kind"] == "header"
    assert json.loads(lines[1]) == {
        "kind": "value",
        "op": "set",
        "seq": 1,
        "namespace": "avid.branch.tip",
        "key": "main",
        "value": None,
    }
    # one append commits two writes (entry + branch tip), so the line is an array
    commit = json.loads(lines[2])
    assert isinstance(commit, list) and len(commit) == 2
    entry, tip = commit
    assert set(entry.keys()) == {"kind", "seq", "timestamp", "id", "parentId", "type", "message"}
    assert entry["kind"] == "entry"
    assert entry["parentId"] is None
    assert entry["message"] == USER
    assert tip == {
        "kind": "value",
        "op": "set",
        "seq": 3,
        "namespace": "avid.branch.tip",
        "key": "main",
        "value": entry["id"],
    }
    session.close()


def test_session_file_name_is_time_first_and_safe(tmp_path):
    name = session_file_name(1_700_000_000_000, "a/b c")
    assert name.startswith("2023-11-14T22-13-20-000_")
    assert name.endswith(".jsonl")
    assert "/" not in name


def test_header_roundtrip_and_rejections():
    header = JsonlHeader(id="s", storage_version=1, created_at=5, parent_session_id="p", next_seq=3)
    parsed = parse_header(encode_header(header))
    assert parsed == header
    with pytest.raises(SessionStorageError):
        parse_header("not json")
    with pytest.raises(SessionStorageError):
        parse_header(json.dumps({"kind": "header", "v": 99, "id": "s", "storageVersion": 1, "createdAt": 1}))
    with pytest.raises(SessionStorageError):
        parse_header(json.dumps({"kind": "entry", "v": 1}))


# ---------------- replay ----------------


def test_reopening_after_restart_sees_the_same_state(tmp_path):
    first = make_repo(tmp_path)
    session = first.create(id="demo")
    session.create_branch("main", None)
    session.branch("main").append_message(USER)
    session.branch("main").append_message(ASSISTANT)
    session.set_name("重启前")
    session.close()
    first.close()

    second = make_repo(tmp_path)
    listed = second.list()
    assert [item.id for item in listed] == ["demo"]
    assert isinstance(listed[0], JsonlSessionMetadata)
    assert listed[0].modified_at > 0
    reopened = second.open(listed[0])
    assert reopened.get_name() == "重启前"
    assert [entry.message for entry in reopened.find_entries(EntryQuery(order="asc"))] == [USER, ASSISTANT]
    assert reopened.get_stats().message_count == 2
    reopened.close()
    second.close()


def test_list_on_missing_root_is_empty(tmp_path):
    repo = make_repo(tmp_path / "never-created")
    assert repo.list() == []
    repo.close()


def test_list_skips_unreadable_and_broken_files(tmp_path):
    repo = make_repo(tmp_path)
    session = repo.create(id="good")
    session.close()
    repo.close()

    (tmp_path / "junk.jsonl").write_text("这不是 JSON\n", encoding="utf-8")
    (tmp_path / "torn.jsonl").write_text('{"kind": "header"', encoding="utf-8")

    fresh = make_repo(tmp_path)
    assert [item.id for item in fresh.list()] == ["good"]
    fresh.close()


def test_list_skips_files_that_cannot_be_read(tmp_path):
    repo = make_repo(tmp_path)
    repo.create(id="good").close()
    repo.close()
    (tmp_path / "weird.jsonl").mkdir()  # named like a session file but unreadable

    fresh = make_repo(tmp_path)
    assert [item.id for item in fresh.list()] == ["good"]
    fresh.close()


def test_duplicate_id_is_detected_by_scanning_the_directory(tmp_path):
    first = make_repo(tmp_path)
    first.create(id="dup").close()
    first.close()

    second = make_repo(tmp_path)
    with pytest.raises(SessionExistsError):
        second.create(id="dup")
    second.close()


def test_delete_removes_the_file(tmp_path):
    repo = make_repo(tmp_path)
    session = repo.create(id="gone")
    session.close()
    meta = repo.list()[0]
    repo.delete(meta)
    assert list(tmp_path.glob("*.jsonl")) == []
    repo.close()


# ---------------- corrupt files ----------------


def test_torn_tail_is_dropped_and_repaired(tmp_path):
    path = tmp_path / "torn.jsonl"
    write_lines(path, encode_header(JsonlHeader("torn", 1, 100)), entry_line(1, "a", None))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "entry", "seq": 2')  # killed mid-line

    storage = JsonlStorage.open(path)
    assert storage.get_entries(["a"])["a"].message == USER
    assert storage.next_seq == 2

    content = path.read_text(encoding="utf-8")
    assert content.endswith("\n")
    assert "seq\": 2" not in content
    storage.close()


def test_corrupt_middle_line_reports_its_number(tmp_path):
    path = tmp_path / "broken.jsonl"
    write_lines(
        path,
        encode_header(JsonlHeader("broken", 1, 100)),
        entry_line(1, "a", None),
        "{不是 JSON",
        entry_line(3, "b", "a"),
    )
    with pytest.raises(SessionStorageError) as info:
        JsonlStorage.open(path)
    assert "第 3 行" in str(info.value)


def test_non_monotonic_seq_is_rejected_on_replay(tmp_path):
    path = tmp_path / "seq.jsonl"
    write_lines(
        path,
        encode_header(JsonlHeader("seq", 1, 100)),
        entry_line(2, "a", None),
        entry_line(2, "b", "a"),  # duplicate seq, but in the middle, not a torn tail
        entry_line(3, "c", "a"),
    )
    with pytest.raises(SessionStorageError) as info:
        JsonlStorage.open(path)
    assert "第 3 行" in str(info.value)


def test_missing_parent_is_rejected_on_replay(tmp_path):
    path = tmp_path / "orphan.jsonl"
    write_lines(
        path,
        encode_header(JsonlHeader("orphan", 1, 100)),
        entry_line(1, "a", "ghost"),
        entry_line(2, "b", "a"),  # keep it in the middle: a bad trailing line is a torn tail
    )
    with pytest.raises(SessionStorageError):
        JsonlStorage.open(path)


def test_a_corrupt_tail_line_is_repaired_instead_of_killing_the_file(tmp_path):
    """A corrupt trailing line (crash short write, duplicate seq from a concurrent writer) heals
    in place; a corrupt middle line still raises, because silently dropping history is worse
    than refusing to open.
    """
    path = tmp_path / "tail.jsonl"
    write_lines(
        path,
        encode_header(JsonlHeader("tail", 1, 100)),
        entry_line(1, "a", None),
        '{"kind": "entry", "seq": 1, "id": "dup"}',  # bad trailing line: duplicate seq
    )

    storage = JsonlStorage.open(path)
    try:
        assert storage.get_entries(["a"])["a"].message == USER
        assert storage.next_seq == 2
    finally:
        storage.close()

    content = path.read_text(encoding="utf-8")
    assert content.endswith("\n")
    assert '"dup"' not in content, "坏末行应被原子重写掉"


def test_missing_header_is_rejected(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SessionStorageError):
        JsonlStorage.open(path)


def test_unknown_storage_version_is_rejected_by_the_repo(tmp_path):
    path = tmp_path / "old.jsonl"
    write_lines(path, encode_header(JsonlHeader("old", 7, 100)))
    repo = make_repo(tmp_path)
    meta = JsonlSessionMetadata(id="old", created_at=100, storage_version=7, path=path)
    with pytest.raises(SessionStorageError):
        repo.open(meta)
    repo.close()


def test_repo_close_closes_open_handles(tmp_path):
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    session.create_branch("main", None)
    repo.close()
    with pytest.raises(SessionClosedError):
        session.get_stats()


def test_tip_value_survives_a_restart(tmp_path):
    first = make_repo(tmp_path)
    session = first.create(id="demo")
    session.create_branch("main", None)
    entry_id = session.branch("main").append_message(USER)
    session.close()
    first.close()

    second = make_repo(tmp_path)
    reopened = second.open(second.list()[0])
    assert reopened.branch("main").get_tip_id() == entry_id
    reopened.close()
    second.close()


# ---------------- workspace ownership ----------------


def test_header_records_the_workspace_when_known(tmp_path):
    repo = JsonlSessionRepo(tmp_path, workspace="w-abc")
    session = repo.create(id="demo")

    header = json.loads(only_file(tmp_path).read_text(encoding="utf-8").splitlines()[0])

    assert header["workspaceId"] == "w-abc"
    assert session.metadata.workspace == "w-abc"
    session.close()
    repo.close()


def test_header_omits_the_workspace_when_unknown(tmp_path):
    """The key is omitted when ownership is unknown, so old and new files share one shape."""
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")

    header = json.loads(only_file(tmp_path).read_text(encoding="utf-8").splitlines()[0])

    assert "workspaceId" not in header
    assert session.metadata.workspace is None
    session.close()


def test_legacy_session_inherits_the_repo_workspace(tmp_path):
    """A legacy session without the field inherits the repo's workspace: location is ownership."""
    make_repo(tmp_path).create(id="legacy").close()

    repo = JsonlSessionRepo(tmp_path, workspace="w-abc")
    listed = repo.list()

    assert listed[0].workspace == "w-abc"
    session = repo.open(listed[0])
    assert session.metadata.workspace == "w-abc"
    session.close()
    repo.close()


def test_open_ignores_a_stale_workspace_label_in_its_own_directory(tmp_path):
    """Re-registering a workspace changes its id, so a stale header label must still open inside
    its own directory: location is ownership, and re-registration must not brick old sessions.
    """
    mine = JsonlSessionRepo(tmp_path, workspace="w-mine")
    session = mine.create(id="demo", workspace="w-other")
    session.close()
    metadata = mine.list()[0]
    mine.close()

    reopened = JsonlSessionRepo(tmp_path, workspace="w-after-re-register")
    assert reopened.open(metadata).metadata.id == "demo"
    reopened.close()

    # the old-label repo still opens by label (both directions unaffected)
    other = JsonlSessionRepo(tmp_path, workspace="w-other")
    assert other.open(metadata).metadata.workspace == "w-other"
    other.close()


def test_open_refuses_a_session_that_lives_in_another_directory(tmp_path):
    """Guard intent: a metadata.path pointing into another directory must not open its file."""
    mine = JsonlSessionRepo(tmp_path / "mine", workspace="w-mine")
    foreign_repo = JsonlSessionRepo(tmp_path / "elsewhere", workspace="w-other")
    session = foreign_repo.create(id="demo")
    session.close()
    metadata = session.metadata
    foreign_repo.close()

    with pytest.raises(SessionStorageError) as exc:
        mine.open(metadata)
    assert "另一个工作区" in str(exc.value)
    mine.close()


# ---------------- branch scan and session lookup cost ----------------


def test_newest_first_scan_stops_before_the_chain_breaks(tmp_path):
    """newestFirst with a limit stops early: with a mid-chain entry removed, limit=2 must not
    reach the break (no error) while oldestFirst must reach the root and raise.
    """
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    ids = [branch.append_message({"role": "user", "content": f"第 {index} 条"}) for index in range(5)]

    state = session._storage._state  # type: ignore[attr-defined]
    broken = ids[2]
    saved = state._entries.pop(broken)  # type: ignore[attr-defined]
    try:
        tip = branch.get_tip_id()
        assert tip == ids[-1]

        recent = session.scan_branch(BranchScan(start=tip, order="newestFirst", limit=2))
        assert [entry.id for entry in recent] == [ids[-1], ids[-2]]

        with pytest.raises(SessionInvariantError):
            session.scan_branch(BranchScan(start=tip, order="oldestFirst"))
    finally:
        state._entries[broken] = saved  # type: ignore[attr-defined]
        session.close()
        repo.close()


def test_a_session_id_is_not_confused_with_another_ids_suffix(tmp_path):
    """create(id="a") must not be blocked by an existing x_a: suffix matching is not identity."""
    repo = make_repo(tmp_path)
    repo.create(id="x_a").close()

    created = repo.create(id="a")  # naive suffix matching would hit ..._x_a.jsonl
    created.close()

    assert sorted(item.id for item in repo.list()) == ["a", "x_a"]
    repo.close()


# ---------------- delete guardrails ----------------


def test_delete_refuses_a_metadata_from_another_session(tmp_path):
    """Delete passes the same identity guard as open: since _locate prefers metadata.path,
    skipping the check would let A's metadata delete B's file, and deletion is less reversible.
    """
    repo = make_repo(tmp_path)
    victim = repo.create(id="victim")
    victim.close()
    repo.create(id="attacker").close()

    victim_meta = next(item for item in repo.list() if item.id == "victim")
    forged = replace(victim_meta, id="attacker")  # points at victim's file, claims attacker

    with pytest.raises(SessionStorageError) as info:
        repo.delete(forged)
    assert "id 与请求不符" in str(info.value)
    assert victim_meta.path.exists(), "护栏拦下时文件必须原样在"

    # normal deletion still works.
    repo.delete(victim_meta)
    assert not victim_meta.path.exists()
    repo.close()


def test_delete_ignores_a_stale_workspace_label_in_its_own_directory(tmp_path):
    """Same location rule as open: a stale label after re-registration does not block deletion."""
    mine = JsonlSessionRepo(tmp_path, workspace="w-mine")
    mine.create(id="demo", workspace="w-old").close()
    metadata = mine.list()[0]
    mine.close()

    reregistered = JsonlSessionRepo(tmp_path, workspace="w-new")
    reregistered.delete(metadata)
    reregistered.close()


def test_delete_refuses_a_session_that_lives_in_another_directory(tmp_path):
    mine = JsonlSessionRepo(tmp_path / "mine", workspace="w-mine")
    foreign_repo = JsonlSessionRepo(tmp_path / "elsewhere", workspace="w-other")
    foreign_repo.create(id="demo").close()
    metadata = foreign_repo.list()[0]
    foreign_repo.close()

    with pytest.raises(SessionStorageError) as info:
        mine.delete(metadata)
    assert "另一个工作区" in str(info.value)
    mine.close()


# ---------------- read-path isolation and concurrency ----------------


def test_readers_get_copies_not_the_internal_state(tmp_path):
    """Readers get copies, not internal state: mutating a returned message must not change the
    session (frozen dataclasses block attribute assignment but not nested mutation).
    """
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    message = {
        "role": "user",
        "content": "原文",
        "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": "{}"}}],
    }
    entry_id = branch.append_message(message)
    session.close()
    repo.close()

    reader = make_repo(tmp_path)
    opened = reader.open(reader.list()[0])
    entry = opened.get_entry(entry_id)
    assert entry is not None and entry.message is not None
    entry.message["content"] = "被改过了"
    entry.message["tool_calls"][0]["function"]["arguments"] = "被改过了"

    fresh = opened.get_entry(entry_id)
    assert fresh is not None and fresh.message is not None
    assert fresh.message["content"] == "原文"
    assert fresh.message["tool_calls"][0]["function"]["arguments"] == "{}"
    opened.close()
    reader.close()


def test_writers_cannot_mutate_the_state_after_committing(tmp_path):
    """A writer mutating its own dict after commit must not affect session state (I1 both ways)."""
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    message = {"role": "user", "content": "提交时的内容"}
    entry_id = branch.append_message(message)

    message["content"] = "提交之后改的"

    stored = session.get_entry(entry_id)
    assert stored is not None and stored.message is not None
    assert stored.message["content"] == "提交时的内容"
    session.close()
    repo.close()


class _SpyLock:
    """Lock double counting __enter__ calls (RLock's context protocol is now these two methods)."""

    def __init__(self) -> None:
        self.entered = 0

    def __enter__(self) -> "_SpyLock":
        self.entered += 1
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_every_read_path_takes_the_state_lock(tmp_path):
    """Every read path takes the state lock (a mechanism assertion, not a race reproduction):
    inserting into a dict while iterating raises RuntimeError and surfaces as HTTP 500, and that
    window is too narrow to reproduce by simply running concurrently.
    """
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    entry_id = branch.append_message(USER)

    state = session._storage._state  # type: ignore[attr-defined]
    spy = _SpyLock()
    state._lock = spy

    calls = {
        "get_stats": lambda: session.get_stats(),
        "get_entry": lambda: session.get_entry(entry_id),
        "scan_values": lambda: session.scan_values("avid.branch.tip"),
        "scan_branch": lambda: session.branch("main").find_entries(
            BranchScan(order="oldestFirst")
        ),
        "scan_entries": lambda: session.find_entries(EntryQuery(limit=5)),
    }
    for label, call in calls.items():
        spy.entered = 0
        call()
        assert spy.entered >= 1, f"{label} 没有进入读锁"
    session.close()
    repo.close()


def test_concurrent_reads_during_commits_do_not_raise(tmp_path):
    """Read/write smoke test: reads during commits raise nothing (the lock assertion above is
    the real mechanism guard, since this window rarely reproduces on its own).
    """
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    for index in range(40):
        branch.append_message({"role": "user", "content": f"第 {index} 条"})

    failures: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                session.get_stats()
                session.branch("main").find_entries(BranchScan(order="oldestFirst"))
                session.find_entries(EntryQuery(limit=10))
                session.get_entry("demo-e1")
            except Exception as exc:  # any exception counts as a failure
                failures.append(f"{type(exc).__name__}: {exc}")
                return

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for thread in threads:
        thread.start()
    try:
        for index in range(80):
            branch.append_message({"role": "assistant", "content": f"回复 {index}"})
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

    assert failures == []
    assert session.get_stats().message_count == 40 + 80
    session.close()
    repo.close()


def test_storage_wraps_unserialisable_and_unencodable_writes(tmp_path):
    """The storage layer only promises SessionError: non-JSON values (TypeError) and surrogate
    pairs (UnicodeEncodeError, a ValueError) are both wrapped, since this layer owns the actual
    file write.
    """
    path = tmp_path / "writes.jsonl"
    write_lines(path, encode_header(JsonlHeader("writes", 1, 100)))
    storage = JsonlStorage.open(path)
    try:
        with pytest.raises(SessionStorageError):
            storage.commit(
                [EntryWrite(NewEntry(id="a", parent_id=None, message={"role": "user", "content": "坏的\ud800"}))]
            )
        with pytest.raises(SessionStorageError):
            storage.commit(
                [EntryWrite(NewEntry(id="a", parent_id=None, message={"role": "user", "extra": {1, 2}}))]
            )
        assert storage.get_stats().message_count == 0, "失败的两条都不该落进去"
        storage.commit([EntryWrite(NewEntry(id="a", parent_id=None, message=USER))])
        assert storage.get_stats().message_count == 1
    finally:
        storage.close()

    # the file stays readable: the failure left no half line behind
    repo = JsonlSessionRepo(tmp_path)
    reopened = repo.open(repo.list()[0])
    assert reopened.get_stats().message_count == 1
    reopened.close()
    repo.close()


def test_repo_commit_of_a_non_json_value_is_still_a_session_error(tmp_path):
    """Through the session layer a non-JSON value is caught earlier, but still as SessionError."""
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    with pytest.raises(SessionInvalidMessageError):
        branch.append_message({"role": "user", "content": "正常", "extra": {1, 2}})
    session.close()
    repo.close()


# ---------------- list summary: name, count and torn tail without replay ----------------

TRAILING_NAME = "最后的名字"


def test_summarize_matches_replay_even_with_marker_literals_in_content(tmp_path):
    """The fast summary matches replay field by field: the count uses substring counting of
    "kind": "entry", and the same literal inside message text is JSON-escaped and not counted.
    """
    path = tmp_path / "summary.jsonl"
    trick = '正文里出现 "kind": "entry" 与 "avid.session.name" 这两个字面量'
    write_lines(
        path,
        encode_header(JsonlHeader("summary", 1, 100)),
        entry_line(1, "a", None),
        entry_line(2, "b", "a"),
        _message_line(
            3,
            "c",
            "a",
            {
                "role": "assistant",
                "content": trick,
                # tool_calls with no results -> torn tail
                "tool_calls": [
                    {
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
        ),
        encode_transaction(
            [CommittedValueSet(4, "avid.session.name", "", "中间的名字")]
        ),
        encode_transaction(
            [CommittedValueSet(5, "avid.session.name", "", TRAILING_NAME)]
        ),
        # the default branch tip points at c, whose tool_calls have no results -> torn tail
        encode_transaction([CommittedValueSet(6, "avid.branch.tip", "main", "c")]),
    )

    summary = summarize_file(path)

    assert summary.name == TRAILING_NAME, "取最后一次写入"
    assert summary.message_count == 3, "正文里的字面量不能被算成条目"
    assert summary.truncated_tail is True

    # field-by-field agreement with the replay path.
    repo = JsonlSessionRepo(tmp_path)
    session = repo.open(repo.list()[0])
    assert summary.name == session.get_name()
    assert summary.message_count == session.get_stats().message_count
    session.close()
    repo.close()


def test_summarize_tail_is_none_when_the_window_cannot_decide(tmp_path):
    """When the window cannot see the chain tip, return None (let the caller replay) not False."""
    path = tmp_path / "short-window.jsonl"
    write_lines(
        path,
        encode_header(JsonlHeader("short", 1, 100)),
        entry_line(1, "a", None),
        encode_transaction([CommittedValueSet(2, "avid.branch.tip", "main", "a")]),
    )

    assert summarize_file(path, window=1).truncated_tail is None
    assert summarize_file(path, window=32).truncated_tail is False


def test_repo_summarize_is_cached_until_the_file_changes(tmp_path):
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    session.set_name("名字")
    branch = session.create_branch("main", None)
    branch.append_message(USER)
    session.close()

    meta = repo.list()[0]
    first = repo.summarize(meta)
    again = repo.summarize(meta)

    assert first is again, "同一份文件第二次应当命中缓存"
    assert (first.name, first.message_count) == ("名字", 1)

    # after an append the stamp changes: reread instead of returning a stale count
    reopened = repo.open(meta)
    reopened.branch("main").append_message(ASSISTANT)
    reopened.close()

    after = repo.summarize(meta)
    assert after.message_count == 2
    repo.close()


# ---------------- cross-process exclusion and write integrity ----------------


def test_a_second_opener_is_locked_out_until_the_first_closes(tmp_path):
    """Two processes each writing a line would duplicate seqs, and replay rejects non-monotonic
    seq, leaving the whole file unreadable; flock is per open file description, so two opens in
    one process exclude each other too and this covers the other-process semantics.
    """
    repo = make_repo(tmp_path)
    repo.create(id="demo").close()
    repo.close()

    path = only_file(tmp_path)
    first = JsonlStorage.open(path)
    try:
        with pytest.raises(SessionLockedError):
            JsonlStorage.open(path)
    finally:
        first.close()

    # after release the file must open again (no lock leak).
    third = JsonlStorage.open(path)
    third.close()


def test_delete_is_locked_out_while_another_holder_exists(tmp_path):
    repo = make_repo(tmp_path)
    repo.create(id="demo").close()
    repo.close()

    path = only_file(tmp_path)
    holder = JsonlStorage.open(path)
    try:
        other = JsonlSessionRepo(tmp_path)
        with pytest.raises(SessionLockedError):
            other.delete(other.list()[0])
        other.close()
    finally:
        holder.close()

    after = JsonlSessionRepo(tmp_path)
    after.delete(after.list()[0])
    after.close()
    assert list(tmp_path.glob("*.jsonl")) == []


def test_a_short_write_is_rolled_back_and_reported(tmp_path, monkeypatch):
    """A short write (ENOSPC, signal) leaves no half line and never advances in-memory state."""
    import os

    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    branch.append_message(USER)

    path = only_file(tmp_path)
    before = path.read_bytes()
    next_seq_before = session.get_stats().message_count

    real_write = os.write

    def short_write(fd: int, data: bytes) -> int:
        # truncate only the session transaction line, leaving other writes in this process alone
        if b'"seq"' in bytes(data):
            return real_write(fd, data[: max(1, len(data) // 2)])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", short_write)
    with pytest.raises(SessionStorageError) as info:
        branch.append_message(ASSISTANT)
    assert "不完整" in str(info.value)

    monkeypatch.undo()
    assert path.read_bytes() == before, "短写必须回滚，不能残留半行"
    assert session.get_stats().message_count == next_seq_before
    session.close()
    repo.close()

    # the file stays readable and appendable
    again = make_repo(tmp_path)
    reopened = again.open(again.list()[0])
    assert reopened.branch("main").append_message(ASSISTANT)
    reopened.close()
    again.close()


# ---------------- workspace guard and lock leaks ----------------


def test_stale_workspace_id_in_header_still_opens_in_its_own_directory(tmp_path):
    """A stale workspace id in the header still opens inside its own directory: location decides
    ownership, and a refusal must not leak the lock.
    """
    repo = make_repo(tmp_path)
    created = repo.create(id="demo", workspace="w-new")
    created.close()
    repo.close()

    reopened = JsonlSessionRepo(tmp_path, workspace="w-new-after-re-register")
    session = reopened.open(created.metadata)
    assert session.metadata.id == "demo"
    session.close()
    reopened.close()


def test_a_failed_guard_open_does_not_leak_the_lock(tmp_path):
    """When the guard refuses (id mismatch) storage must close first, or the flock leaks and the
    session can never be opened again in this process.
    """
    repo = make_repo(tmp_path)
    repo.create(id="demo").close()

    wrong = replace(repo.list()[0], id="another-id")
    with pytest.raises(SessionStorageError):
        repo.open(wrong)

    # the lock must be released: the same session opens again immediately.
    session = repo.open(repo.list()[0])
    session.close()


def test_a_symlinked_foreign_session_is_invisible_to_this_repo(tmp_path):
    """Ownership uses resolve(): a foreign session symlinked into this directory is neither
    listed nor openable through the link.
    """
    mine = JsonlSessionRepo(tmp_path / "mine", workspace="w-mine")
    foreign_repo = JsonlSessionRepo(tmp_path / "elsewhere", workspace="w-other")
    foreign_repo.create(id="secret").close()
    foreign_meta = foreign_repo.list()[0]

    (tmp_path / "mine").mkdir(exist_ok=True)
    link = tmp_path / "mine" / "0link_secret.jsonl"
    link.symlink_to(foreign_meta.path)
    mine.close()
    mine = JsonlSessionRepo(tmp_path / "mine", workspace="w-mine")

    assert [m.id for m in mine.list()] == []

    smuggled = replace(foreign_meta, path=link)
    with pytest.raises(SessionStorageError):
        mine.open(smuggled)
    with pytest.raises(SessionStorageError):
        mine.delete(smuggled)
    mine.close()
    foreign_repo.close()


# ---------------- list hot path: facts cached, resolve() only for symlinks ----------------
#
# The sidebar lists all sessions on every refresh, so the two cases below are hot-path
# guardrails: red means the per-file realpath and header parsing work came back.


def test_listing_twice_parses_each_header_once(tmp_path, monkeypatch):
    from avid.session import jsonl as jsonl_module

    repo = make_repo(tmp_path)
    for index in range(3):
        repo.create(id=f"s-{index}").close()
    reads: list[str] = []
    real = jsonl_module.read_header

    def spy(path):
        reads.append(str(path))
        return real(path)

    monkeypatch.setattr(jsonl_module, "read_header", spy)

    first = repo.list()
    assert len(first) == 3
    assert len(reads) == 3

    repo.list()

    assert len(reads) == 3  # the second listing reads no header at all
    repo.close()


def test_listing_does_not_resolve_every_entry(tmp_path, monkeypatch):
    """Resolving only matters for symlinks: plain files must not go through realpath."""
    repo = make_repo(tmp_path)
    for index in range(3):
        repo.create(id=f"s-{index}").close()
    resolved: list[str] = []
    real = Path.resolve

    def spy(self, *args, **kwargs):
        resolved.append(str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", spy)

    repo.list()

    per_entry = [item for item in resolved if item.endswith(".jsonl")]
    assert per_entry == [], per_entry
    repo.close()


def test_a_changed_file_refreshes_its_remembered_facts(tmp_path):
    import os

    repo = make_repo(tmp_path)
    repo.create(id="s-fact").close()
    before = repo.list()[0]

    path = before.path
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 5_000_000_000))

    after = repo.list()[0]

    assert after.modified_at == before.modified_at + 5000
    repo.close()


def test_a_deleted_file_drops_out_of_the_next_listing(tmp_path):
    repo = make_repo(tmp_path)
    repo.create(id="s-gone").close()
    kept = repo.create(id="s-keep")
    kept.close()

    next(item for item in repo.list() if item.id == "s-gone").path.unlink()

    assert [item.id for item in repo.list()] == ["s-keep"]
    repo.close()


def test_an_unreadable_file_is_not_remembered_as_unreadable(tmp_path):
    """A failed parse is not cached: once the file is fixed the next listing must see it."""
    repo = make_repo(tmp_path)
    repo.create(id="s-ok").close()
    broken = tmp_path / "half.jsonl"
    broken.write_text('{"kind": "header"', encoding="utf-8")

    assert [item.id for item in repo.list()] == ["s-ok"]

    tick = clock()
    broken.write_text(
        encode_header(
            JsonlHeader(id="s-fixed", storage_version=1, created_at=tick())
        )
        + "\n",
        encoding="utf-8",
    )

    assert sorted(item.id for item in repo.list()) == ["s-fixed", "s-ok"]
    repo.close()


def test_deleted_sessions_do_not_stay_remembered(tmp_path):
    """In a long-lived process (the web service) deletions must leave no cache entry: each
    listing prunes them.
    """
    repo = make_repo(tmp_path)
    repo.create(id="s-keep").close()
    dropped = repo.create(id="s-drop")
    dropped.close()
    repo.list()
    assert len(repo._metadata) == 2

    next(item for item in repo.list() if item.id == "s-drop").path.unlink()
    repo.list()

    assert [name for name in repo._metadata if "s-drop" in name] == []
    assert len(repo._metadata) == 1
    repo.close()
