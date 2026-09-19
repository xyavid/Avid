import json

import pytest

from avid.cli import AGENT_TOOL_HELP, build_parser
from avid.tools import TOOL_IMPLS, TOOLS
from avid.tools.schemas import tool
from avid.tools.validate import validate_arguments

NAMES = [item["function"]["name"] for item in TOOLS]
VALID_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
PARAMETERS = {item["function"]["name"]: item["function"]["parameters"] for item in TOOLS}


def test_definitions_and_implementations_match():
    assert sorted(NAMES) == sorted(TOOL_IMPLS)


def test_expected_tools_are_registered():
    assert sorted(NAMES) == [
        "bash",
        "can_start",
        "claim_task",
        "complete_task",
        "create_task",
        "edit_file",
        "get_task",
        "glob",
        "load_skill",
        "read_file",
        "subagent",
        "todo_write",
        "update_task",
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
