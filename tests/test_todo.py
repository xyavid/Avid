import pytest

from avid.tools import todo as todo_module
from avid.tools.todo import (
    TODO_REMINDER_AFTER_ROUNDS,
    TodoList,
    bind,
    build_reminder,
    current,
    todo_write,
)


@pytest.fixture
def todo():
    with bind(TodoList()) as bound:
        yield bound


# ---------- 状态模型 ----------


def test_starts_empty(todo):
    assert todo.items == []
    assert todo.render() == "（列表为空）"
    assert todo.counts() == {"pending": 0, "in_progress": 0, "completed": 0}


def test_write_replaces_the_whole_list(todo):
    result = todo_write({"todos": [{"content": "第一步", "status": "in_progress"}]})

    assert "已更新 TODO" in result
    assert todo.items == [{"content": "第一步", "status": "in_progress"}]

    todo_write({"todos": [{"content": "只剩这一步", "status": "pending"}]})

    assert todo.items == [{"content": "只剩这一步", "status": "pending"}]


def test_render_marks_each_status(todo):
    todo_write(
        {
            "todos": [
                {"content": "已做", "status": "completed"},
                {"content": "在做", "status": "in_progress"},
                {"content": "没做", "status": "pending"},
            ]
        }
    )

    assert todo.render() == "[x] 1. 已做\n[~] 2. 在做\n[ ] 3. 没做"
    assert todo.counts() == {"pending": 1, "in_progress": 1, "completed": 1}


def test_content_is_stripped(todo):
    todo_write({"todos": [{"content": "  两边有空格  ", "status": "pending"}]})

    assert todo.items[0]["content"] == "两边有空格"


def test_empty_list_clears(todo):
    todo_write({"todos": [{"content": "a", "status": "pending"}]})

    assert "已清空" in todo_write({"todos": []})
    assert todo.items == []


# ---------- 更新规则：整体替换 + 原子校验 ----------


def test_invalid_status_rejects_the_whole_update(todo):
    todo_write({"todos": [{"content": "保留", "status": "pending"}]})

    result = todo_write({"todos": [{"content": "新的", "status": "doing"}]})

    assert result.startswith("错误：")
    assert "doing" in result
    assert todo.items == [{"content": "保留", "status": "pending"}]


def test_blank_content_rejects_the_whole_update(todo):
    result = todo_write({"todos": [{"content": "   ", "status": "pending"}]})

    assert "content 不能为空" in result
    assert todo.items == []


def test_one_bad_item_rejects_the_good_ones_too(todo):
    result = todo_write(
        {
            "todos": [
                {"content": "合法", "status": "pending"},
                {"content": "非法", "status": "完成"},
            ]
        }
    )

    assert result.startswith("错误：")
    assert todo.items == []


def test_non_list_is_rejected(todo):
    assert todo_write({"todos": "不是数组"}).startswith("错误：")


def test_item_must_be_an_object(todo):
    assert "不是对象" in todo_write({"todos": ["字符串"]})


def test_missing_argument_is_rejected(todo):
    assert todo_write({}).startswith("错误：")


def test_only_one_in_progress_allowed(todo):
    result = todo_write(
        {
            "todos": [
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ]
        }
    )

    assert "只能有一项 in_progress" in result
    assert "2 项" in result
    assert todo.items == []


def test_write_outside_a_bound_run_is_rejected():
    assert "没有活动的 TODO 列表" in todo_write({"todos": []})


# ---------- 运行隔离 ----------


def test_binding_is_isolated_per_run():
    first = TodoList()
    second = TodoList()

    with bind(first):
        todo_write({"todos": [{"content": "属于第一次运行", "status": "pending"}]})

    with bind(second):
        assert second.items == []
        assert current() is second

    assert first.items == [{"content": "属于第一次运行", "status": "pending"}]


def test_binding_is_restored_after_the_block():
    assert current() is None

    with bind(TodoList()) as bound:
        assert current() is bound

    assert current() is None


# ---------- reminder ----------


def test_reminder_mentions_the_count_and_current_list(todo):
    todo_write({"todos": [{"content": "剩下的活", "status": "pending"}]})

    reminder = build_reminder(todo, 3)

    assert reminder.startswith("[提醒]")
    assert "连续 3 轮" in reminder
    assert "剩下的活" in reminder


def test_reminder_works_with_an_empty_list(todo):
    assert "列表为空" in build_reminder(todo, TODO_REMINDER_AFTER_ROUNDS)


def test_threshold_is_a_single_constant():
    assert todo_module.TODO_REMINDER_AFTER_ROUNDS == 3
