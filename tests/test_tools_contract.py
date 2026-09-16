import pytest

from avid.tools import TOOLS, TOOL_IMPLS
from avid.tools.schemas import tool

NAMES = [item["function"]["name"] for item in TOOLS]
VALID_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


def test_definitions_and_implementations_match():
    assert sorted(NAMES) == sorted(TOOL_IMPLS)


def test_expected_tools_are_registered():
    assert sorted(NAMES) == [
        "bash",
        "edit_file",
        "glob",
        "load_skill",
        "read_file",
        "subagent",
        "todo_write",
        "write_file",
    ]


def test_names_are_unique():
    assert len(NAMES) == len(set(NAMES))


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
