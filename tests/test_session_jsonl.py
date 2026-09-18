"""文件后端：格式、重放、撕裂行、损坏行与目录扫描。

这些是被内存后端遮住、只有真的落在磁盘上才会暴露的规则。一致性由
``test_session_conformance.py`` 管，这里管格式本身。
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
        entry_line(2, "b", "a"),  # 重复 seq —— 但它在**中间**，不是尾残片
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
        entry_line(2, "b", "a"),  # 让它落在中间：尾部的坏行按残片处理
    )
    with pytest.raises(SessionStorageError):
        JsonlStorage.open(path)


def test_a_corrupt_tail_line_is_repaired_instead_of_killing_the_file(tmp_path):
    """末行坏了（崩溃短写 / 并发写留下的重复 seq）时**自愈**。

    以前这会让整个文件此后不可读、会话从列表里静默消失——那正是 P0 的后果。
    中间行坏掉仍然报错：静默丢历史比拒绝打开更危险。
    """
    path = tmp_path / "tail.jsonl"
    write_lines(
        path,
        encode_header(JsonlHeader("tail", 1, 100)),
        entry_line(1, "a", None),
        '{"kind": "entry", "seq": 1, "id": "dup"}',  # 坏末行：seq 重复
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


# ---------------- 删除的护栏（P1-15） ----------------


def test_delete_refuses_a_metadata_from_another_session(tmp_path):
    """删除也要过 open 的同一套身份护栏。

    `_locate` 优先用 `metadata.path`：不校验的话，拿 A 的 metadata 就能删掉 B 的
    文件——删除比打开更不可逆，这里不能比 open 更松。
    """
    repo = make_repo(tmp_path)
    victim = repo.create(id="victim")
    victim.close()
    repo.create(id="attacker").close()

    victim_meta = next(item for item in repo.list() if item.id == "victim")
    forged = replace(victim_meta, id="attacker")  # 指向 victim 的文件，但自称 attacker

    with pytest.raises(SessionStorageError) as info:
        repo.delete(forged)
    assert "id 与请求不符" in str(info.value)
    assert victim_meta.path.exists(), "护栏拦下时文件必须原样在"

    # 正常删除仍然可用。
    repo.delete(victim_meta)
    assert not victim_meta.path.exists()
    repo.close()


def test_delete_refuses_a_session_from_another_workspace(tmp_path):
    mine = JsonlSessionRepo(tmp_path, workspace="w-mine")
    mine.create(id="demo").close()  # 归属 w-mine
    metadata = mine.list()[0]
    mine.close()

    other = JsonlSessionRepo(tmp_path, workspace="w-other")
    with pytest.raises(SessionStorageError) as info:
        other.delete(metadata)
    assert "另一个工作区" in str(info.value)
    other.close()


# ---------------- 读路径的隔离与并发（P1-16 / P1-17） ----------------


def test_readers_get_copies_not_the_internal_state(tmp_path):
    """读出来的对象不能是内部状态本身：改写它不该改变会话。

    `Entry` 是 frozen dataclass，但 frozen 只挡属性赋值——`entry.message["x"] = …`
    与嵌套的 tool_calls 都改得动。不变量 I1（只增不改）以前只对文件成立。
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
    """写入方提交后改写自己那份 dict，也不该影响会话状态（I1 是双向的）。"""
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
    """记录被进入次数的锁替身（RLock 的上下文协议就这两个方法）。"""

    def __init__(self) -> None:
        self.entered = 0

    def __enter__(self) -> "_SpyLock":
        self.entered += 1
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_every_read_path_takes_the_state_lock(tmp_path):
    """读路径必须与写互斥（机制断言，不靠竞态复现）。

    dict 在迭代中被插入会抛 ``RuntimeError: dictionary changed size during
    iteration``（例如 `values_in` 遍历值表时另一个线程提交了一条分支头值），
    表现为 HTTP 500。这个窗口很窄——用"并发跑一会儿看有没有异常"复现不可靠，
    所以这里直接断言每条读路径都进入了 `SessionState` 的锁；上面那条并发用例
    留作冒烟。
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
    """并发读写的冒烟：读路径在提交进行中反复跑，不允许冒出任何异常。

    真正的机制由上面那条断言锁的用例守着（这个窗口很窄，光靠并发不一定复现）。
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
            except Exception as exc:  # 任何异常都算失败
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
    """存储层只承诺抛 SessionError：非 JSON 值（TypeError）与代理对字符
    （UnicodeEncodeError，是 ValueError 的子类）都要收敛，不能漏出契约。

    消息级校验（session.py 的 validate_message）会先拦下非 JSON 值，所以这里直接
    打存储层——它才是"写文件"这一步的所有者，也是代理对唯一会炸的地方。
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

    # 文件仍然可读（失败没有留下半行）。
    repo = JsonlSessionRepo(tmp_path)
    reopened = repo.open(repo.list()[0])
    assert reopened.get_stats().message_count == 1
    reopened.close()
    repo.close()


def test_repo_commit_of_a_non_json_value_is_still_a_session_error(tmp_path):
    """走会话层时非 JSON 值被更早的校验拦下，但错误类型同样是 SessionError。"""
    repo = make_repo(tmp_path)
    session = repo.create(id="demo")
    branch = session.create_branch("main", None)
    with pytest.raises(SessionInvalidMessageError):
        branch.append_message({"role": "user", "content": "正常", "extra": {1, 2}})
    session.close()
    repo.close()


# ---------------- 列表页摘要：不重放也能给出名字/条数/链尾残缺 ----------------

TRAILING_NAME = "最后的名字"


def test_summarize_matches_replay_even_with_marker_literals_in_content(tmp_path):
    """快速摘要必须与重放逐字段一致，尤其是正文里含标记字面量时。

    条数靠子串计数（一个条目写恰好一次 `"kind": "entry"`），而消息正文里的
    同名字面量会被 JSON 转义，因此不该被算进去——这条耦合就在这里钉住。
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
                # 一批没有结果的 tool_calls → 链尾残缺
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
        # 默认分支链尾指向 c；c 是一批没有结果的 tool_calls → 链尾残缺。
        encode_transaction([CommittedValueSet(6, "avid.branch.tip", "main", "c")]),
    )

    summary = summarize_file(path)

    assert summary.name == TRAILING_NAME, "取最后一次写入"
    assert summary.message_count == 3, "正文里的字面量不能被算成条目"
    assert summary.truncated_tail is True

    # 与重放路径逐字段对齐。
    repo = JsonlSessionRepo(tmp_path)
    session = repo.open(repo.list()[0])
    assert summary.name == session.get_name()
    assert summary.message_count == session.get_stats().message_count
    session.close()
    repo.close()


def test_summarize_tail_is_none_when_the_window_cannot_decide(tmp_path):
    """窗口内看不到链尾时返回 None（交给调用方重放），而不是猜一个 False。"""
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

    # 追加一条之后 stamp 变了：必须重新读，不能返回陈旧条数。
    reopened = repo.open(meta)
    reopened.branch("main").append_message(ASSISTANT)
    reopened.close()

    after = repo.summarize(meta)
    assert after.message_count == 2
    repo.close()


# ---------------- 跨进程互斥与写入完整性（P0） ----------------


def test_a_second_opener_is_locked_out_until_the_first_closes(tmp_path):
    """两个进程各写一行会让 seq 重复，而重放拒绝非单调 seq——整个文件此后不可读。

    flock 是按 open file description 生效的，所以同一个进程里开两次也会互斥，
    测试因此能覆盖"另一个进程"的语义。
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

    # 释放之后必须能再打开（锁不能泄漏）。
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
    """短写（ENOSPC / 信号）不能留下半行，也不能让内存状态偷偷推进。"""
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
        # 只截断会话事务那一行，避免影响同一进程里的其它写入。
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

    # 文件仍然可读，且能继续追加。
    again = make_repo(tmp_path)
    reopened = again.open(again.list()[0])
    assert reopened.branch("main").append_message(ASSISTANT)
    reopened.close()
    again.close()
