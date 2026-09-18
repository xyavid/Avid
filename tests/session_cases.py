"""会话仓库的一致性套件。

一套用例，参数化到所有后端（内存 / JSONL）——行为一致靠测试而不是靠文档，
这是参考实现最值得抄的一个设计（``testing/conformance``）。用例只碰
``SessionRepo`` 的公开方法，因此后端任何一处语义漂移都会在这里露出来。

本模块不以 ``test_`` 开头，pytest 不会收集它；它只提供 ``all_cases()``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from avid.session import (
    BranchScan,
    EntryQuery,
    EntryWrite,
    NewEntry,
    SessionAlreadyOpenError,
    SessionBranchExistsError,
    SessionBusyError,
    SessionClosedError,
    SessionError,
    SessionExistsError,
    SessionInvalidMessageError,
    SessionInvariantError,
    SessionNotFoundError,
    SessionUnknownTargetError,
    SessionStats,
    STORAGE_VERSION,
    branch_tip,
    set_value,
    session_name,
    value,
)

USER = {"role": "user", "content": "一"}
ASSISTANT = {"role": "assistant", "content": "二"}


@dataclass(frozen=True)
class Case:
    group: str
    name: str
    run: Callable[[Any], None]


# ---------------- 生命周期 ----------------


def _create_has_no_branch(repo) -> None:
    session = repo.create(id="s1")
    assert session.metadata.id == "s1"
    assert session.metadata.created_at > 0
    assert session.metadata.storage_version == STORAGE_VERSION
    # create 不隐式建分支：要写内容必须先显式建一条。
    assert session.branch("main") is None
    assert session.get_value(branch_tip("main")) is None
    with pytest.raises(SessionExistsError):
        repo.create(id="s1")
    session.close()


def _close_then_reopen_keeps_state(repo) -> None:
    first = repo.create(id="a")
    first.create_branch("main", None)
    first.branch("main").append_message(USER)
    first.set_name("保留的名字")
    second = repo.create(id="b", parent_session_id="parent")
    second.close()

    listed = {item.id: item for item in repo.list()}
    assert set(listed) == {"a", "b"}
    assert listed["b"].parent_session_id == "parent"
    with pytest.raises(SessionAlreadyOpenError):
        repo.open(first.metadata)

    first.close()
    with pytest.raises(SessionClosedError):
        first.get_name()
    with pytest.raises(SessionClosedError):
        first.branch("main")
    with pytest.raises(SessionClosedError):
        first.find_entries()

    reopened = repo.open(first.metadata)
    assert reopened is not first
    assert reopened.get_name() == "保留的名字"
    assert [entry.message for entry in reopened.find_entries()] == [USER]
    reopened.close()


def _delete_only_closed_sessions(repo) -> None:
    removed = repo.create(id="removed")
    kept = repo.create(id="kept")
    kept.close()

    with pytest.raises(SessionAlreadyOpenError):
        repo.delete(removed.metadata)

    removed.close()
    repo.delete(removed.metadata)
    assert [item.id for item in repo.list()] == ["kept"]
    with pytest.raises(SessionNotFoundError):
        repo.open(removed.metadata)
    with pytest.raises(SessionNotFoundError):
        repo.delete(removed.metadata)


def _open_is_exclusive(repo) -> None:
    session = repo.create(id="s")
    with pytest.raises(SessionAlreadyOpenError):
        repo.open(session.metadata)
    session.close()

    reopened = repo.open(session.metadata)
    with pytest.raises(SessionAlreadyOpenError):
        repo.open(session.metadata)
    reopened.close()


def _repo_close_seals(repo) -> None:
    session = repo.create(id="s")
    session.close()
    repo.close()
    with pytest.raises(SessionClosedError):
        repo.create(id="t")
    with pytest.raises(SessionClosedError):
        repo.list()
    with pytest.raises(SessionClosedError):
        repo.open(session.metadata)


def _list_is_newest_first(repo) -> None:
    for name in ("one", "two", "three"):
        session = repo.create(id=name)
        session.close()
    assert [item.id for item in repo.list()] == ["three", "two", "one"]


def _invalid_ids_rejected(repo) -> None:
    from avid.session import SessionInvalidIdError

    for bad in ("", "   ", "a/b", "a\\b", "a\u0000b"):
        with pytest.raises(SessionInvalidIdError):
            repo.create(id=bad)


# ---------------- 消息与分支 ----------------


def _append_requires_branch(repo) -> None:
    session = repo.create(id="s")
    with pytest.raises(SessionInvariantError):
        session.append_message("main", USER)

    branch = session.create_branch("main", None)
    assert branch.get_tip_id() is None
    assert branch.find_entries() == []

    first = branch.append_message(USER)
    second = branch.append_message(ASSISTANT)
    assert branch.get_tip_id() == second

    entries = branch.find_entries(BranchScan(order="oldestFirst"))
    assert [entry.id for entry in entries] == [first, second]
    assert entries[0].parent_id is None
    assert entries[1].parent_id == first
    assert session.get_entry(first).message == USER
    assert session.get_entry("never").__class__ is type(None)
    assert session.get_stats() == SessionStats(message_count=2)
    session.close()


def _branches_are_independent(repo) -> None:
    session = repo.create(id="s")
    left = session.create_branch("left", None)
    right = session.create_branch("right", None)
    first = left.append_message(USER)

    assert right.get_tip_id() is None
    assert right.find_entries() == []
    assert [entry.id for entry in left.find_entries()] == [first]

    with pytest.raises(SessionBranchExistsError):
        session.create_branch("left", None)
    with pytest.raises(SessionUnknownTargetError):
        session.create_branch("beyond", "missing")

    beyond = session.create_branch("beyond", first)
    assert beyond.get_tip_id() == first
    assert [entry.id for entry in beyond.find_entries(BranchScan(order="oldestFirst"))] == [first]
    session.close()


def _invalid_messages_are_rejected(repo) -> None:
    session = repo.create(id="s")
    branch = session.create_branch("main", None)
    bad_messages: list[Any] = [
        "不是字典",
        {"role": "system", "content": "x"},
        {"role": "tool", "content": "x"},
        {"role": "assistant", "tool_calls": [{"name": "read_file"}]},
        {"role": "user", "content": object()},
    ]
    for message in bad_messages:
        with pytest.raises(SessionInvalidMessageError):
            branch.append_message(message)

    assert branch.get_tip_id() is None
    assert session.find_entries() == []
    assert session.get_stats().message_count == 0
    session.close()


def _append_is_atomic_with_the_branch_tip(repo) -> None:
    """分支头与条目同一次提交：末尾永远指得到刚写进去的那一条。"""
    session = repo.create(id="s")
    branch = session.create_branch("main", None)
    ids = [branch.append_message({"role": "user", "content": f"第{index}条"}) for index in range(3)]
    assert branch.get_tip_id() == ids[-1]
    chain = branch.find_entries(BranchScan(order="oldestFirst"))
    assert [entry.parent_id for entry in chain] == [None] + ids[:-1]
    session.close()


# ---------------- 变更能力 ----------------


def _mutation_commits_exactly_once(repo) -> None:
    session = repo.create(id="s")
    session.create_branch("main", None)

    mutator = session.begin_mutation()
    result = mutator.commit([set_value(session_name(), "第一次")])
    assert len(result.seqs) == 1
    with pytest.raises(SessionError):
        mutator.commit([set_value(session_name(), "第二次")])
    assert session.get_name() == "第一次"
    mutator.end()
    with pytest.raises(SessionError):
        mutator.commit([set_value(session_name(), "第三次")])
    with pytest.raises(SessionError):
        mutator.get_value(session_name())
    assert session.get_name() == "第一次"

    # 零次提交也是合法的：变更可以什么都不写。
    session.begin_mutation().end()
    session.close()


def _nested_mutation_reports_instead_of_deadlocking(repo) -> None:
    session = repo.create(id="s")
    session.create_branch("main", None)

    with pytest.raises(SessionBusyError):
        session.mutate(lambda mutator: session.set_name("嵌套"))

    def job(mutator) -> None:
        with pytest.raises(SessionBusyError):
            session.begin_mutation()
        with pytest.raises(SessionBusyError):
            session.close()

    session.mutate(job)
    assert session.closed is False
    session.close()
    assert session.closed is True


def _failed_commits_consume_nothing(repo) -> None:
    session = repo.create(id="s")
    branch = session.create_branch("main", None)
    first = branch.append_message(USER)
    before_ids = [entry.id for entry in session.find_entries(EntryQuery(order="asc"))]
    before_stats = session.get_stats()

    def duplicate_id(mutator) -> None:
        mutator.commit(
            [EntryWrite(NewEntry(id=first, parent_id=None, message=ASSISTANT))]
        )

    def missing_parent(mutator) -> None:
        mutator.commit(
            [EntryWrite(NewEntry(id="orphan", parent_id="missing", message=USER))]
        )

    with pytest.raises(SessionInvariantError):
        session.mutate(duplicate_id)
    with pytest.raises(SessionInvariantError):
        session.mutate(missing_parent)

    assert [entry.id for entry in session.find_entries(EntryQuery(order="asc"))] == before_ids
    assert session.get_stats() == before_stats
    assert branch.get_tip_id() == first

    # 一次失败的提交不消耗 seq：下一次成功的提交紧接着上一次成功提交之后。
    address = value("test.app.counter")
    before = session.mutate(lambda mutator: mutator.commit([set_value(address, 0)]))
    with pytest.raises(SessionInvariantError):
        session.mutate(missing_parent)
    after = session.mutate(lambda mutator: mutator.commit([set_value(address, 1)]))
    assert after.first_seq == before.first_seq + len(before.seqs)
    assert session.get_value(address).value == 1
    session.close()


# ---------------- 值 ----------------


def _values_roundtrip(repo) -> None:
    session = repo.create(id="s")
    address = value("test.app.state")
    assert session.get_value(address) is None

    session.set_value(address, {"v": 1})
    stored = session.get_value(address)
    assert stored.value == {"v": 1}
    assert stored.seq >= 1

    session.set_value(address, "覆盖")
    assert session.get_value(address).value == "覆盖"

    session.delete_value(address)
    assert session.get_value(address) is None

    session.set_label("entry-x", "标签")
    assert session.get_label("entry-x") == "标签"
    session.set_label("entry-x", None)
    assert session.get_label("entry-x") is None
    session.set_name(None)
    assert session.get_name() is None
    session.close()


def _value_namespace_scan_is_ordered_and_isolated(repo) -> None:
    """``scan_values`` 只按命名空间枚举，且按 seq 升序（分支列表依赖这两条）。"""
    session = repo.create(id="s")
    first = value("test.a", "one")
    other = value("test.b", "elsewhere")
    second = value("test.a", "two")

    assert session.scan_values("test.a") == []
    session.set_value(first, 1)
    session.set_value(other, 2)
    session.set_value(second, 3)

    found = session.scan_values("test.a")
    assert [item.key for item in found] == ["one", "two"]
    assert [item.value for item in found] == [1, 3]
    # 升序而不是插入顺序的巧合：seq 必须严格递增
    assert found[0].seq < found[1].seq

    session.delete_value(first)
    assert [item.key for item in session.scan_values("test.a")] == ["two"]
    assert [item.key for item in session.scan_values("test.b")] == ["elsewhere"]
    assert session.scan_values("test.missing") == []

    # 分支头就是这个机制的第一个真实消费者：main 建好之后必须能被枚举出来
    assert session.branch_names() == ["main"]
    session.create_branch("b2", None)
    assert session.branch_names() == ["main", "b2"]
    session.close()


# ---------------- 查询 ----------------


def _entry_queries_page_and_filter(repo) -> None:
    session = repo.create(id="s")
    branch = session.create_branch("main", None)
    ids = [
        branch.append_message({"role": "user", "content": f"第{index}条"})
        for index in range(1, 4)
    ]

    newest = session.find_entries()
    assert [entry.id for entry in newest] == list(reversed(ids))
    assert len(session.find_entries(EntryQuery(limit=2))) == 2

    asc = session.find_entries(EntryQuery(order="asc", limit=2))
    assert [entry.id for entry in asc] == ids[:2]
    page = session.find_entries(EntryQuery(order="asc", cursor_seq=asc[-1].seq))
    assert [entry.id for entry in page] == [ids[2]]
    assert session.find_entries(EntryQuery(order="asc", cursor_seq=page[-1].seq)) == []

    desc_page = session.find_entries(EntryQuery(cursor_seq=newest[0].seq))
    assert [entry.id for entry in desc_page] == [ids[1], ids[0]]

    assert session.find_entry().id == ids[-1]
    assert session.find_entry(EntryQuery(order="asc")).id == ids[0]
    assert session.find_entries(EntryQuery(type="compaction")) == []
    session.close()


def _branch_scan_orders_limits_and_pages(repo) -> None:
    session = repo.create(id="s")
    branch = session.create_branch("main", None)
    for index in range(3):
        branch.append_message({"role": "user", "content": f"第{index}条"})

    newest = branch.find_entries()
    assert [entry.seq for entry in newest] == sorted(
        (entry.seq for entry in newest), reverse=True
    )
    oldest = branch.find_entries(BranchScan(order="oldestFirst"))
    assert [entry.seq for entry in oldest] == sorted(entry.seq for entry in oldest)
    assert len(branch.find_entries(BranchScan(limit=2))) == 2
    assert [
        entry.id
        for entry in branch.find_entries(
            BranchScan(order="oldestFirst", cursor_seq=oldest[0].seq)
        )
    ] == [oldest[1].id, oldest[2].id]
    assert branch.find_entry().id == newest[0].id
    assert branch.find_entries(BranchScan(type="message")) == newest
    assert branch.find_entries(BranchScan(type="compaction")) == []
    session.close()


def all_cases() -> list[Case]:
    return [
        Case("lifecycle", "create 不隐式建分支并拒绝重复 id", _create_has_no_branch),
        Case("lifecycle", "close 之后句柄失效且 reopen 保留状态", _close_then_reopen_keeps_state),
        Case("lifecycle", "delete 只对已关闭会话生效", _delete_only_closed_sessions),
        Case("lifecycle", "列表按创建时间从新到旧", _list_is_newest_first),
        Case("lifecycle", "非法 id 被拒", _invalid_ids_rejected),
        Case("ownership", "同一会话只允许一个打开中的句柄", _open_is_exclusive),
        Case("ownership", "仓库关闭后不再接受任何操作", _repo_close_seals),
        Case("messages", "写入前必须先建分支", _append_requires_branch),
        Case("messages", "分支之间互不干扰", _branches_are_independent),
        Case("messages", "非法消息被拒且不改动状态", _invalid_messages_are_rejected),
        Case("messages", "条目与分支头同事务", _append_is_atomic_with_the_branch_tip),
        Case("mutation", "变更恰好提交一次且 end 后作废", _mutation_commits_exactly_once),
        Case("mutation", "嵌套变更报错而不是自锁", _nested_mutation_reports_instead_of_deadlocking),
        Case("mutation", "失败的提交什么都不消耗", _failed_commits_consume_nothing),
        Case("values", "值的读写删除与标签", _values_roundtrip),
        Case("values", "命名空间枚举有序且互不串门", _value_namespace_scan_is_ordered_and_isolated),
        Case("queries", "条目查询的翻页与过滤", _entry_queries_page_and_filter),
        Case("queries", "分支扫描的顺序、上限与翻页", _branch_scan_orders_limits_and_pages),
    ]
