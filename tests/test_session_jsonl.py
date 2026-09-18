"""文件后端：格式、重放、撕裂行、损坏行与目录扫描。

这些是被内存后端遮住、只有真的落在磁盘上才会暴露的规则。一致性由
``test_session_conformance.py`` 管，这里管格式本身。
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from avid.session import (
    EntryQuery,
    JsonlSessionRepo,
    JsonlSessionMetadata,
    SessionClosedError,
    SessionExistsError,
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
)
from avid.session.types import CommittedEntry, NewEntry

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


# ---------------- 格式 ----------------


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
    # append 一次提交两写（条目 + 分支头），因此这一行是数组——同事务的格式证据。
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


# ---------------- 重放 ----------------


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
    (tmp_path / "weird.jsonl").mkdir()  # 名字像会话文件，但读不了

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


# ---------------- 坏文件 ----------------


def test_torn_tail_is_dropped_and_repaired(tmp_path):
    path = tmp_path / "torn.jsonl"
    write_lines(path, encode_header(JsonlHeader("torn", 1, 100)), entry_line(1, "a", None))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "entry", "seq": 2')  # 被杀在半行上

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
        entry_line(2, "b", "a"),
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
    )
    with pytest.raises(SessionStorageError):
        JsonlStorage.open(path)


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


# ---------------- 工作区归属（阶段 18） ----------------


def test_header_records_the_workspace_when_known(tmp_path):
    repo = JsonlSessionRepo(tmp_path, workspace="w-abc")
    session = repo.create(id="demo")

    header = json.loads(only_file(tmp_path).read_text(encoding="utf-8").splitlines()[0])

    assert header["workspaceId"] == "w-abc"
    assert session.metadata.workspace == "w-abc"
    session.close()
    repo.close()


def test_header_omits_the_workspace_when_unknown(tmp_path):
    """没有归属时不发射这个键：老文件与新文件的形状因此一致（可选字段惯例）。"""
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")

    header = json.loads(only_file(tmp_path).read_text(encoding="utf-8").splitlines()[0])

    assert "workspaceId" not in header
    assert session.metadata.workspace is None
    session.close()


def test_legacy_session_inherits_the_repo_workspace(tmp_path):
    """老会话没有这个字段：按仓库归属补上——位置即归属。"""
    make_repo(tmp_path).create(id="legacy").close()

    repo = JsonlSessionRepo(tmp_path, workspace="w-abc")
    listed = repo.list()

    assert listed[0].workspace == "w-abc"
    session = repo.open(listed[0])
    assert session.metadata.workspace == "w-abc"
    session.close()
    repo.close()


def test_open_refuses_a_session_from_another_workspace(tmp_path):
    """护栏：metadata 带着别的工作区时不许静默打开（_locate 会优先用 metadata.path）。"""
    mine = JsonlSessionRepo(tmp_path, workspace="w-mine")
    session = mine.create(id="demo", workspace="w-other")
    session.close()
    metadata = mine.list()[0]
    mine.close()

    other = JsonlSessionRepo(tmp_path, workspace="w-other")
    assert other.open(metadata).metadata.workspace == "w-other"
    other.close()

    with pytest.raises(SessionStorageError) as exc:
        mine2 = JsonlSessionRepo(tmp_path, workspace="w-mine")
        mine2.open(metadata)
    assert "另一个工作区" in str(exc.value)
