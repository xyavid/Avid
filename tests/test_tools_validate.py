"""参数校验：模型看到的 schema 与运行时校验同源（P1-1）。

只查结构约束（required / type / enum / 数值边界）；跨字段业务规则仍留在工具自己的
领域校验里。校验失败是**协议错误**：回文本、不抛异常、不触发事件、不计入工具调用。
"""

from __future__ import annotations

from typing import Any

from avid.runtime.execution import execute_one
from avid.runtime.hooks import HookRegistry
from avid.runtime.state import RunState
from avid.tools import TOOLS
from avid.tools.validate import validate_arguments


def params_of(name: str) -> dict[str, Any]:
    """拿到发给模型的那份 parameters 节点——校验用的就是它。"""
    return next(item for item in TOOLS if item["function"]["name"] == name)["function"][
        "parameters"
    ]


def test_valid_arguments_pass():
    assert validate_arguments(params_of("read_file"), {"path": "a.txt", "offset": 2}) is None


def test_missing_required_names_the_field():
    problem = validate_arguments(params_of("read_file"), {})

    assert problem is not None
    assert problem.startswith("参数错误：")
    assert "path" in problem
    assert "缺失" in problem


def test_wrong_type_says_expected_and_actual():
    problem = validate_arguments(
        params_of("read_file"), {"path": "a.txt", "offset": "abc"}
    )

    assert problem is not None
    assert "offset" in problem
    assert "integer" in problem
    assert "string" in problem
    assert "请按工具 schema 修正后重试" in problem


def test_boolean_is_not_accepted_as_integer():
    """`True` 在 Python 里是 int 的子类，JSON 里却不是 integer。"""
    problem = validate_arguments(params_of("read_file"), {"path": "a.txt", "offset": True})

    assert problem is not None
    assert "boolean" in problem


def test_enum_is_enforced_for_nested_items():
    problem = validate_arguments(
        params_of("todo_write"),
        {"todos": [{"content": "写测试", "status": "done"}]},
    )

    assert problem is not None
    assert "todos[0].status" in problem
    assert "pending" in problem


def test_nested_required_is_enforced():
    problem = validate_arguments(params_of("todo_write"), {"todos": [{"status": "pending"}]})

    assert problem is not None
    assert "todos[0].content" in problem


def test_extra_keys_are_tolerated():
    """多带的键现在被实现忽略；收紧它属于行为变更，这条钉住"暂时不收紧"。"""
    assert validate_arguments(params_of("read_file"), {"path": "a.txt", "extra": 1}) is None


def test_execute_one_rejects_bad_arguments_before_running_the_tool():
    calls: list[dict[str, Any]] = []

    def runner(arguments: dict[str, Any], *, state: Any = None) -> str:
        calls.append(arguments)
        return "不该执行到这里"

    state = RunState.for_run(hooks=HookRegistry(), auto_approve=True)
    content = execute_one(
        "read_file",
        '{"offset": "abc"}',
        {"read_file": runner},
        state=state,
        round_index=0,
        parameters=params_of("read_file"),
    )

    assert content.startswith("参数错误：")
    assert calls == [], "参数不合 schema 时不该进到实现里"
    assert state.tool_calls == 0, "协议错误不计入工具调用"


def test_execute_one_without_schema_skips_validation():
    """直调路径（单测、复用某个工具）不传 parameters，行为与改动前一致。"""
    seen: list[Any] = []

    def runner(arguments: dict[str, Any], *, state: Any = None) -> str:
        seen.append(arguments)
        return "ok"

    state = RunState.for_run(hooks=HookRegistry(), auto_approve=True)
    content = execute_one(
        "read_file",
        '{"offset": "abc"}',
        {"read_file": runner},
        state=state,
        round_index=0,
    )

    assert content == "ok"
    assert seen == [{"offset": "abc"}]
