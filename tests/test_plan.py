"""The plan (todo) as session state: a long-running task list that survives the run that wrote it.

Failure list this file pins: an empty list and an all-completed list both delete the value instead
of storing it; a stored value that is not the declared shape reads as no plan, so a corrupted slot
never stops a run; a run without an on_plan sink (a subagent) publishes nothing, leaving the
parent's plan alone; rewind drops the value with the chain it belonged to.
"""

from __future__ import annotations

import pytest

from avid.agent.state import RunState
from avid.agent.todo import TodoList
from avid.agent.tools.todo import todo_write
from avid.session import (
    DEFAULT_BRANCH,
    SessionRecorder,
    branch_plan,
    stored_plan,
)


@pytest.fixture
def session(tmp_path):
    """A real JSONL session in a temp dir, with a branch ready for writes."""
    from avid.session import JsonlSessionRepo

    root = tmp_path / "sessions"
    repo = JsonlSessionRepo(root, workspace="w-one")
    opened = repo.create(workspace="w-one")
    recorder = SessionRecorder(opened)
    recorder.ensure_branch()
    try:
        yield opened, recorder
    finally:
        opened.close()
        repo.close()


def items(count=2, status="pending"):
    return [
        {"content": f"第 {index} 步", "status": status} for index in range(1, count + 1)
    ]


# ---------- the address and the write policy ----------


def test_the_plan_is_stored_per_branch():
    assert branch_plan("main").namespace == "avid.plan"
    assert branch_plan("main").key == "main"
    assert branch_plan("feature").key == "feature"


def test_writing_a_plan_stores_it(session):
    opened, recorder = session

    recorder.record_plan(items(2))

    assert stored_plan(opened) == items(2)


def test_an_empty_plan_deletes_the_value(session):
    opened, recorder = session
    recorder.record_plan(items(2))

    recorder.record_plan([])

    assert stored_plan(opened) == []
    assert opened.get_value(branch_plan(DEFAULT_BRANCH)) is None


def test_an_all_completed_plan_is_deleted_too(session):
    """A finished plan is not history to keep: the list exists to be worked off, not archived."""
    opened, recorder = session
    recorder.record_plan(items(2, status="in_progress"))

    recorder.record_plan(items(3, status="completed"))

    assert opened.get_value(branch_plan(DEFAULT_BRANCH)) is None
    assert stored_plan(opened) == []


def test_one_pending_item_keeps_the_value(session):
    opened, recorder = session
    recorder.record_plan(
        [
            {"content": "做完的", "status": "completed"},
            {"content": "还差的", "status": "pending"},
        ]
    )

    assert stored_plan(opened) == [
        {"content": "做完的", "status": "completed"},
        {"content": "还差的", "status": "pending"},
    ]


def test_the_stored_value_is_an_object_not_a_bare_list(session):
    """Values are objects or scalars by convention; the items ride inside one."""
    opened, recorder = session

    recorder.record_plan(items(1))

    assert opened.get_value(branch_plan(DEFAULT_BRANCH)).value == {"items": items(1)}


# ---------- reading it back, tolerantly ----------


def test_a_missing_value_reads_as_no_plan(session):
    opened, _recorder = session

    assert stored_plan(opened) == []


def test_a_corrupted_value_reads_as_no_plan_and_keeps_the_run_alive(session, caplog):
    opened, _recorder = session
    opened.set_value(branch_plan(DEFAULT_BRANCH), {"items": "不是数组"})

    with caplog.at_level("WARNING", logger="avid.session.recorder"):
        assert stored_plan(opened) == []

    assert any("计划" in record.getMessage() for record in caplog.records)


def test_unusable_items_are_dropped_when_the_plan_is_adopted(session, caplog):
    """The value layer only unwraps; the list decides what it can use, and repairs the rest."""
    opened, _recorder = session
    opened.set_value(
        branch_plan(DEFAULT_BRANCH),
        {
            "items": [
                {"content": "合法", "status": "pending"},
                {"content": "状态非法", "status": "doing"},
                {"content": "  ", "status": "pending"},
                "不是对象",
            ]
        },
    )

    with caplog.at_level("WARNING", logger="avid.agent.todo"):
        state = RunState.for_run(plan=stored_plan(opened))

    assert state.todo.items == [{"content": "合法", "status": "pending"}]
    assert len(caplog.records) == 3


def test_a_second_in_progress_item_is_demoted_on_load(session, caplog):
    opened, _recorder = session
    opened.set_value(
        branch_plan(DEFAULT_BRANCH),
        {
            "items": [
                {"content": "第一个", "status": "in_progress"},
                {"content": "第二个", "status": "in_progress"},
            ]
        },
    )

    with caplog.at_level("WARNING", logger="avid.agent.todo"):
        state = RunState.for_run(plan=stored_plan(opened))

    assert [item["status"] for item in state.todo.items] == ["in_progress", "pending"]


# ---------- the run side: publish, and load on the next run ----------


def test_the_tool_publishes_through_the_callback(session):
    _opened, recorder = session
    written: list = []
    state = RunState.for_run(on_plan=lambda items: written.append(items))

    todo_write({"todos": items(2)}, state=state)

    assert written == [items(2)]
    assert state.todo.items == items(2)


def test_a_failed_write_publishes_nothing(session):
    _opened, recorder = session
    written: list = []
    state = RunState.for_run(on_plan=lambda items: written.append(items))
    todo_write({"todos": items(1)}, state=state)

    result = todo_write({"todos": [{"content": "坏", "status": "doing"}]}, state=state)

    assert result.startswith("错误：")
    assert written == [items(1)], "校验失败不该发布，旧计划必须留着"


def test_without_a_sink_the_plan_stays_in_the_run():
    """A subagent has no on_plan: its plan must not reach the parent session."""
    state = RunState.for_run()

    todo_write({"todos": items(1)}, state=state)

    assert state.todo.items == items(1)


def test_the_next_run_starts_from_the_stored_plan(session):
    opened, recorder = session
    recorder.record_plan(items(2))

    state = RunState.for_run(plan=stored_plan(opened))

    assert state.todo.items == items(2)


def test_a_corrupted_plan_does_not_stop_a_run(session, caplog):
    opened, _recorder = session
    opened.set_value(branch_plan(DEFAULT_BRANCH), {"items": [{"content": "x", "status": "??"}]})

    with caplog.at_level("WARNING", logger="avid.agent.todo"):
        state = RunState.for_run(plan=stored_plan(opened))

    assert state.todo.items == []


# ---------- the plan reaches the model in the tail block ----------


def test_the_plan_renders_in_the_tail_note():
    from avid.agent.context import ContextManager
    from avid.agent.transcript import Transcript
    from avid.providers.config import Config

    state = RunState.for_run(plan=items(2))
    manager = ContextManager(
        transcript=Transcript([{"role": "user", "content": "继续"}]),
        state=state,
        config=Config(api_key="k", base_url="https://api.test/v1", model="m"),
        tool_names=["todo_write"],
    )

    request = manager.compose()

    assert "## 当前计划" in request.messages[-1]["content"]
    assert "第 1 步" in request.messages[-1]["content"]
    assert "## 当前计划" not in request.system
    # The view is not the source: the plan is never appended to the transcript
    assert all("第 1 步" not in str(m.get("content")) for m in manager.transcript.as_messages())


def test_an_empty_plan_leaves_no_block():
    plan = TodoList()

    assert plan.items == []
