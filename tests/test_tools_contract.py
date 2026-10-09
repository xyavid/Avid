import inspect
import json

import pytest

from avid.agent.tools import TOOL_IMPLS, TOOLS
from avid.agent.tools.schemas import tool
from avid.agent.tools.validate import validate_arguments
from avid.cli import AGENT_TOOL_HELP, build_parser

NAMES = [item["function"]["name"] for item in TOOLS]
VALID_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
PARAMETERS = {item["function"]["name"]: item["function"]["parameters"] for item in TOOLS}


def _integer_specs(spec):
    """递归找出所有 integer 参数节点（含数组 items 与嵌套对象 properties）。"""
    if not isinstance(spec, dict):
        return
    if spec.get("type") == "integer":
        yield spec
    for sub in (spec.get("properties") or {}).values():
        yield from _integer_specs(sub)
    if isinstance(spec.get("items"), dict):
        yield from _integer_specs(spec["items"])


def test_definitions_and_implementations_match():
    assert sorted(NAMES) == sorted(TOOL_IMPLS)


def test_stateful_tools_are_exactly_the_handlers_that_take_state():
    """`STATEFUL_TOOLS` 是可枚举的事实：需要运行状态的工具必须真的接受 `state=`。

    `execution.py` 的 docstring 一直声称这条由契约测试守着，但此前只有一条针对 6 个
    任务工具的子集断言。漏进这张表（或反过来多写一个名字）会让执行时抛 TypeError，
    再被兜底成「工具执行失败」——错误信息指向工具，根因却在注册表。
    """
    from avid.agent.execution import STATEFUL_TOOLS

    takes_state = {
        name
        for name, impl in TOOL_IMPLS.items()
        if "state" in inspect.signature(impl).parameters
    }

    assert set(STATEFUL_TOOLS) == takes_state


def test_security_side_tool_tables_only_name_registered_tools():
    """安全层的工具名表不许出现已删除或拼错的工具名（静默失效的规则等于没有规则）。

    ``APPROVAL_RULES`` 随阶段 51 删除；剩下的工具名表是 ``PATH_TOOLS`` / ``WRITE_TOOLS``
    （判定目标路径与读写类别），它们同样必须只列已注册的工具。
    """
    from avid.security.action import PATH_TOOLS, WRITE_TOOLS

    assert set(PATH_TOOLS) <= set(NAMES)
    assert set(WRITE_TOOLS) <= set(NAMES)


def test_concurrency_tables_are_a_partition_of_the_registry():
    """并发分类必须是注册表的一个**划分**：每个工具恰好表态一次。

    这张表决定"批内谁和谁能同时跑"（阶段 25）。漏写一个名字的后果是它按独占处理
    （慢，但安全）；但**两张表都写**或**表里出现不存在的名字**说明分类在漂移——
    到时候没人知道某个工具到底安不安全，所以让它红在契约测试里。
    """
    from avid.agent.tools.safety import CONCURRENCY_SAFE, CONDITIONAL, EXCLUSIVE

    assert not (CONCURRENCY_SAFE & EXCLUSIVE), "同一个工具不能既安全又独占"
    assert not (CONCURRENCY_SAFE & CONDITIONAL), "conditional 是第三种，不是 safe 的别名"
    assert not (EXCLUSIVE & CONDITIONAL)
    assert set(TOOL_IMPLS) == CONCURRENCY_SAFE | EXCLUSIVE | CONDITIONAL


def test_concurrency_safe_tools_are_read_only_by_name():
    """并发安全的一侧不许混进"写"类工具（名字级护栏，防手滑挪表）。

    真正判"会不会写"要靠人，这里只把最容易搞错的那几个钉住：写文件、跑命令、
    改任务/待办、派子 agent 全都在独占侧。
    """
    from avid.agent.tools.safety import CONCURRENCY_SAFE, CONDITIONAL, EXCLUSIVE

    assert {
        "write_file",
        "edit_file",
        "todo_write",
        "subagent",
    } <= EXCLUSIVE
    assert {
        "read_file",
        "glob",
        "grep_search",
        "load_skill",
    } <= CONCURRENCY_SAFE
    # bash 是「按调用判」的那一档：只读命令可以并行，写命令仍然独占（阶段 58）。
    assert "bash" in CONDITIONAL


@pytest.mark.parametrize("item", TOOLS, ids=NAMES)
def test_integer_parameters_declare_a_lower_bound(item):
    """整数参数必须有下界：无界 integer 让模型可以传 0 或负数，只能靠实现各自兜底。"""
    for spec in _integer_specs(item["function"]["parameters"]):
        assert isinstance(spec.get("minimum"), int), spec


def test_timeout_parameter_matches_the_enforced_cap():
    """`timeout_seconds` 的上界以前只写在描述里，实现里另有一份 clamp（300 秒）。"""
    from avid.agent.tools import shell

    spec = PARAMETERS["bash"]["properties"]["timeout_seconds"]

    assert spec["minimum"] == 1
    assert spec["maximum"] == shell.MAX_TIMEOUT


def test_expected_tools_are_registered():
    assert sorted(NAMES) == [
        "bash",
        "edit_file",
        "glob",
        "grep_search",
        "load_skill",
        "read_file",
        "subagent",
        "todo_write",
        "write_file",
    ]


def test_names_are_unique():
    assert len(NAMES) == len(set(NAMES))


def test_placeholder_args_stay_schema_valid():
    """测试用的占位参数表必须与 schema 同步。

    `support.PLACEHOLDER_ARGS` 被大量 loop / svc / web 用例当前提：某一项一旦不合
    schema，这些用例就会静默走到"参数错误"分支，测的就不再是它们声称的那条路径。
    """
    from support import PLACEHOLDER_ARGS

    for name in NAMES:
        assert name in PLACEHOLDER_ARGS, f"占位参数表少了 {name}"
        problem = validate_arguments(PARAMETERS[name], json.loads(PLACEHOLDER_ARGS[name]))
        assert problem is None, f"{name} 的占位参数不合 schema：{problem}"


@pytest.mark.parametrize("item", TOOLS, ids=NAMES)
def test_envelope_is_complete(item):
    assert item["type"] == "function"

    function = item["function"]
    assert function["name"]
    assert function["description"].strip()

    parameters = function["parameters"]
    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    assert isinstance(parameters["required"], list)


@pytest.mark.parametrize("item", TOOLS, ids=NAMES)
def test_required_parameters_are_declared(item):
    parameters = item["function"]["parameters"]

    assert set(parameters["required"]) <= set(parameters["properties"])


@pytest.mark.parametrize("item", TOOLS, ids=NAMES)
def test_every_parameter_documents_type_and_meaning(item):
    for name, spec in item["function"]["parameters"]["properties"].items():
        assert spec["type"] in VALID_TYPES, name
        assert spec["description"].strip(), name


@pytest.mark.parametrize("item", TOOLS, ids=NAMES)
def test_array_parameters_declare_their_items(item):
    """数组参数必须写清元素结构，否则模型只能猜。"""
    for name, spec in item["function"]["parameters"]["properties"].items():
        if spec["type"] != "array":
            continue
        assert spec["items"]["type"], name
        if spec["items"]["type"] == "object":
            assert spec["items"]["required"], name
            assert spec["items"]["additionalProperties"] is False, name


def test_builder_defaults_required_to_empty():
    built = tool("demo", "说明", {"x": {"type": "string", "description": "参数"}})

    assert built["function"]["parameters"]["required"] == []


def test_agent_help_lists_exactly_the_registered_tools():
    """help 曾两次与注册表脱节（写 8 个时实际 14 个），钉住「派生而非手抄」。

    argparse 会在空白处折行，所以比较前先去掉全部空白：这样既要求每个工具都在
    help 里，也要求它不多列任何已有工具之外的名字。
    """
    help_text = "".join(build_parser().format_help().split())

    assert "".join(AGENT_TOOL_HELP.split()) in help_text
    for name in NAMES:
        assert name in help_text, name
