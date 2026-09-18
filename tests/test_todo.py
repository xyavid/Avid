import inspect

import pytest

from avid.policy.todo import (
    TodoList,
    build_reminder,
    todo_write,
)
from avid.runtime.state import RunState


@pytest.fixture
def state():
    return RunState()


def write(state, todos):
    return todo_write({"todos": todos}, state=state)


# ---------- 状态模型 ----------


def test_starts_empty(state):
    assert state.todo.items == []
    assert state.todo.render() == "（列表为空）"
    assert state.todo.counts() == {"pending": 0, "in_progress": 0, "completed": 0}


def test_write_replaces_the_whole_list(state):
    result = write(state, [{"content": "第一步", "status": "in_progress"}])

    assert "已更新 TODO" in result
    assert state.todo.items == [{"content": "第一步", "status": "in_progress"}]

    write(state, [{"content": "只剩这一步", "status": "pending"}])

    assert state.todo.items == [{"content": "只剩这一步", "status": "pending"}]


def test_render_marks_each_status(state):
    write(
        state,
        [
            {"content": "已做", "status": "completed"},
            {"content": "在做", "status": "in_progress"},
            {"content": "没做", "status": "pending"},
        ],
    )

    assert state.todo.render() == "[x] 1. 已做\n[~] 2. 在做\n[ ] 3. 没做"
    assert state.todo.counts() == {"pending": 1, "in_progress": 1, "completed": 1}


def test_content_is_stripped(state):
    write(state, [{"content": "  两边有空格  ", "status": "pending"}])

    assert state.todo.items[0]["content"] == "两边有空格"


def test_empty_list_clears(state):
    write(state, [{"content": "a", "status": "pending"}])

    assert "已清空" in write(state, [])
    assert state.todo.items == []


# ---------- 更新规则：整体替换 + 原子校验 ----------


def test_invalid_status_rejects_the_whole_update(state):
    write(state, [{"content": "保留", "status": "pending"}])

    result = write(state, [{"content": "新的", "status": "doing"}])

    assert result.startswith("错误：")
    assert "doing" in result
    assert state.todo.items == [{"content": "保留", "status": "pending"}]


def test_blank_content_rejects_the_whole_update(state):
    result = write(state, [{"content": "   ", "status": "pending"}])

    assert "content 不能为空" in result
    assert state.todo.items == []


def test_one_bad_item_rejects_the_good_ones_too(state):
    result = write(
        state,
        [
            {"content": "合法", "status": "pending"},
            {"content": "非法", "status": "完成"},
        ],
    )

    assert result.startswith("错误：")
    assert state.todo.items == []


def test_non_list_is_rejected(state):
    assert write(state, "不是数组").startswith("错误：")


def test_item_must_be_an_object(state):
    assert "不是对象" in write(state, ["字符串"])


def test_missing_argument_is_rejected(state):
    assert todo_write({}, state=state).startswith("错误：")


def test_only_one_in_progress_allowed(state):
    result = write(
        state,
        [
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ],
    )

    assert "只能有一项 in_progress" in result
    assert "2 项" in result
    assert state.todo.items == []


# ---------- 运行隔离：显式 RunState，不再是隐式全局状态 ----------


def test_two_runs_do_not_share_todo_state():
    first, second = RunState(), RunState()

    write(first, [{"content": "属于第一次运行", "status": "pending"}])

    assert first.todo.items == [{"content": "属于第一次运行", "status": "pending"}]
    assert second.todo.items == []


def test_todo_write_requires_an_explicit_state():
    """工具不再自己去全局注册表找状态——拿不到就该报错，而不是静默用错的那份。"""
    parameters = inspect.signature(todo_write).parameters

    assert "state" in parameters
    assert parameters["state"].default is inspect.Parameter.empty


def test_run_state_instances_own_their_own_list():
    assert isinstance(RunState().todo, TodoList)
    assert RunState().todo is not RunState().todo


# ---------- reminder ----------


def test_reminder_mentions_the_count_and_current_list(state):
    write(state, [{"content": "剩下的活", "status": "pending"}])

    reminder = build_reminder(state.todo, 3)

    assert reminder.startswith("[提醒]")
    assert "连续 3 轮" in reminder
    assert "剩下的活" in reminder


def test_reminder_threshold_lives_in_run_state():
    from avid.runtime.state import TODO_REMINDER_AFTER_ROUNDS

    assert TODO_REMINDER_AFTER_ROUNDS == 3


def test_reminder_works_with_an_empty_list(state):
    assert "列表为空" in build_reminder(state.todo, 3)


