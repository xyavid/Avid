"""Argument validation: the schema the model sees is the one enforced at runtime, limited to
structural constraints (required / type / enum / numeric bounds).

A failed check is a protocol error: text back, no exception, no event, no tool call.
"""

from __future__ import annotations

from typing import Any

from avid.agent.execution import execute_one
from avid.agent.hooks import HookRegistry
from avid.agent.state import RunState
from avid.agent.tools import TOOLS
from avid.agent.tools.validate import validate_arguments


def params_of(name: str) -> dict[str, Any]:
    """The `parameters` node sent to the model — the same object validation reads."""
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
    """`True` subclasses int in Python but is not a JSON integer."""
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
    """Extra keys are ignored by the implementation; this pins that they stay tolerated."""
    assert validate_arguments(params_of("read_file"), {"path": "a.txt", "extra": 1}) is None


def test_bounds_are_enforced():
    """Numeric bounds live in the schema and the validator must enforce them."""
    too_small = validate_arguments(params_of("read_file"), {"path": "a.txt", "offset": 0})
    assert too_small is not None
    assert "不能小于 1" in too_small

    too_big = validate_arguments(
        params_of("bash"), {"command": "ls", "timeout_seconds": 9999}
    )
    assert too_big is not None
    assert "不能大于 300" in too_big

    assert (
        validate_arguments(params_of("bash"), {"command": "ls", "timeout_seconds": 300})
        is None
    )


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
    """The direct path (unit tests, tool reuse) passes no parameters and skips validation."""
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
