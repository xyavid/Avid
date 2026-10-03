"""registry 单点登记的契约（阶段 30b）。

原契约测试（test_tools_contract.py）钉的是"派生出来的表自洽"；这里钉的是
**登记侧**：一个工具的全部事实（schema、实现、并发分类、是否需要 state）只在
一处声明，其余一切都是派生。
"""

import inspect

import pytest

from avid.agent.tools import SUB_HANDLERS, SUB_TOOLS, TOOL_IMPLS, TOOLS
from avid.agent.tools.registry import ToolSpec, specs


def test_every_tool_is_declared_exactly_once():
    names = [spec.name for spec in specs()]
    assert sorted(names) == sorted(TOOL_IMPLS)
    assert len(names) == len(set(names))


def test_schema_list_is_derived_from_specs():
    assert [spec.schema() for spec in specs()] == TOOLS


def test_stateful_flag_matches_the_implementation_signature():
    for spec in specs():
        takes_state = "state" in inspect.signature(spec.impl).parameters
        assert spec.stateful == takes_state, spec.name


def test_concurrency_is_total_and_explicit():
    for spec in specs():
        assert spec.concurrency in ("safe", "exclusive"), spec.name


def test_subagent_set_excludes_itself():
    assert "subagent" not in {item["function"]["name"] for item in SUB_TOOLS}
    assert "subagent" not in SUB_HANDLERS


def test_duplicate_registration_is_rejected():
    from avid.agent.tools import registry

    def dummy(args):  # noqa: ANN001
        return ""

    with pytest.raises(ValueError, match="重复登记"):
        registry._register(
            ToolSpec(
                name="read_file",
                description="x",
                parameters={"type": "object", "properties": {}, "required": []},
                impl=dummy,
                concurrency="safe",
                stateful=False,
            )
        )


def test_decorator_requires_concurrency_classification():
    """漏表态并发安全性的声明在登记时就报错，而不是执行时才歧义。"""
    from avid.agent.tools.registry import tool

    with pytest.raises(TypeError):

        @tool(name="demo_missing", description="x", properties={})
        def demo(args):  # noqa: ANN001
            return ""


def test_stateful_defaults_to_the_signature():
    from avid.agent.tools.registry import tool

    @tool(name="demo_state_probe", description="x", properties={}, concurrency="safe")
    def with_state(args, *, state):  # noqa: ANN001
        return ""

    @tool(name="demo_bare_probe", description="x", properties={}, concurrency="safe")
    def bare(args):  # noqa: ANN001
        return ""

    spec = {s.name: s for s in specs()}
    assert spec["demo_state_probe"].stateful is True
    assert spec["demo_bare_probe"].stateful is False

    # 测试登记的工具不留在进程注册表里
    from avid.agent.tools import registry

    registry._SPECS[:] = [
        s for s in registry._SPECS if not s.name.startswith("demo_")
    ]
