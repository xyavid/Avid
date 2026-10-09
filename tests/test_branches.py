"""Branch semantics at the service layer, without HTTP: listing, forking, isolation, resuming.

A branch is only a tip value and the entry tree is append-only, so the assertions are about
chains: entries before a fork belong to both branches, later ones stay private.
"""

from __future__ import annotations

import threading

import pytest
from support import ScriptedChat, bound_workspace, make_turn, wait_terminal

from avid.services import Services
from avid.services.errors import BranchExists, InvalidRequest, SessionBusy


def build(root, chat, **kwargs) -> Services:
    return Services(root=root / ".avid" / "sessions", chat=chat, **kwargs)


def run(services: Services, session_id: str, prompt: str = "跑一下", **kwargs):
    record = services.runs.start(session_id, prompt, **kwargs)
    assert wait_terminal(record), f"运行没结束：{record.status}"
    return record


def entry_ids(services: Services, session_id: str, branch: str = "main") -> list[str]:
    page = services.sessions.entries(session_id, branch=branch, order="asc")
    return [entry["entry_id"] for entry in page["entries"]]


def test_fresh_session_exposes_main_as_implicit_default(sandbox):
    """``create`` makes no branch, but readers must treat main as the default."""
    services = build(sandbox, ScriptedChat(make_turn("你好")))
    session_id = services.sessions.create(workspace=bound_workspace(services), name="分支")["id"]

    listed = services.sessions.list_branches(session_id)
    assert [item["name"] for item in listed["branches"]] == ["main"]
    main = listed["branches"][0]
    assert main["is_default"] is True
    assert main["tip_entry_id"] is None
    assert main["entry_count"] == 0


def test_fork_copies_the_prefix_and_keeps_the_rest_private(sandbox):
    chats = ScriptedChat(make_turn("第一轮"), make_turn("第二轮"), make_turn("分支上的第三轮"))
    services = build(sandbox, chats)
    session_id = services.sessions.create(workspace=bound_workspace(services), name="分叉")["id"]
    run(services, session_id, "第一次")
    run(services, session_id, "第二次")

    main_before = entry_ids(services, session_id, "main")
    assert len(main_before) == 4  # two user + two assistant

    # fork at the first assistant: the new chain is the first two entries
    fork_at = main_before[1]
    created = services.sessions.create_branch(session_id, at=fork_at)
    assert created["name"] == "b2"
    assert created["tip_entry_id"] == fork_at
    assert created["is_default"] is False
    assert created["entry_count"] == 2
    assert entry_ids(services, session_id, "b2") == main_before[:2]

    # run on b2: only b2 moves, main is untouched
    run(services, session_id, "分支上继续", branch="b2")
    assert entry_ids(services, session_id, "main") == main_before

    b2_after = entry_ids(services, session_id, "b2")
    assert b2_after[:2] == main_before[:2], "分叉点之前的链条必须重合"
    assert len(b2_after) == 4
    assert not set(b2_after[2:]) & set(main_before), "分叉之后各自私有"

    listed = {item["name"]: item for item in services.sessions.list_branches(session_id)["branches"]}
    assert set(listed) == {"main", "b2"}
    assert listed["main"]["tip_entry_id"] == main_before[-1]
    assert listed["b2"]["tip_entry_id"] == b2_after[-1]
    assert listed["b2"]["entry_count"] == 4


def test_auto_names_skip_taken_ones(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("x")))
    session_id = services.sessions.create(workspace=bound_workspace(services))["id"]

    services.sessions.create_branch(session_id, name="b2")
    third = services.sessions.create_branch(session_id)

    assert third["name"] == "b3", "默认命名必须跳过已占用的名字"
    names = [item["name"] for item in services.sessions.list_branches(session_id)["branches"]]
    assert names == ["main", "b2", "b3"]


def test_duplicate_name_and_unknown_fork_point_are_rejected(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("x")))
    session_id = services.sessions.create(workspace=bound_workspace(services))["id"]
    services.sessions.create_branch(session_id, name="dup")

    with pytest.raises(BranchExists):
        services.sessions.create_branch(session_id, name="dup")
    with pytest.raises(InvalidRequest):
        services.sessions.create_branch(session_id, name="orphan", at="e_missing")


def test_run_creates_a_missing_branch_and_keeps_it_isolated(sandbox):
    """Running on a missing branch starts an empty one from zero (recorder's ensure_branch)."""
    services = build(sandbox, ScriptedChat(make_turn("主线答复"), make_turn("只在分支上")))
    session_id = services.sessions.create(workspace=bound_workspace(services))["id"]
    run(services, session_id, "第一句")
    main_entries = entry_ids(services, session_id, "main")

    run(services, session_id, "换条链", branch="side")
    side_entries = entry_ids(services, session_id, "side")

    assert len(side_entries) == 2, "side 从零开始：只有这次运行的 user + assistant"
    assert not set(side_entries) & set(main_entries), "两条链没有任何共同条目"
    assert entry_ids(services, session_id, "main") == main_entries
    names = [item["name"] for item in services.sessions.list_branches(session_id)["branches"]]
    assert names == ["main", "side"]


def test_fork_is_refused_while_a_run_is_active(sandbox):
    """Forking writes the branch tip while a run thread writes the same chain: no overlap."""
    entered = threading.Event()
    release = threading.Event()

    def blocking_chat(config, messages, **kwargs):
        entered.set()
        release.wait(timeout=5)
        return make_turn("完事")

    services = build(sandbox, blocking_chat)
    session_id = services.sessions.create(workspace=bound_workspace(services))["id"]
    record = services.runs.start(session_id, "占住会话")
    assert entered.wait(timeout=5), "运行没走到模型调用"

    try:
        with pytest.raises(SessionBusy):
            services.sessions.create_branch(session_id, name="during-run")
    finally:
        release.set()
        assert wait_terminal(record)

    # forking is legal once the run has finished
    assert services.sessions.create_branch(session_id, name="after-run")["name"] == "after-run"
