"""Tool registry contracts: definitions vs implementations, schema completeness, derived tables."""

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
    """Yield every integer parameter node, including array items and nested object properties."""
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
    """`STATEFUL_TOOLS` must be the handlers taking `state=`: a missing or extra name becomes
    a runtime TypeError masked as a tool failure."""
    from avid.agent.execution import STATEFUL_TOOLS

    takes_state = {
        name
        for name, impl in TOOL_IMPLS.items()
        if "state" in inspect.signature(impl).parameters
    }

    assert set(STATEFUL_TOOLS) == takes_state


def test_security_side_tool_tables_only_name_registered_tools():
    """Security-side name tables may only list registered tools: a stale name fails silently."""
    from avid.security.action import PATH_TOOLS, WRITE_TOOLS

    assert set(PATH_TOOLS) <= set(NAMES)
    assert set(WRITE_TOOLS) <= set(NAMES)


def test_concurrency_tables_are_a_partition_of_the_registry():
    """Concurrency classes must be a partition of the registry: a missing name falls back to
    exclusive, but a name in two tables or in none means the classification has drifted."""
    from avid.agent.tools.safety import CONCURRENCY_SAFE, CONDITIONAL, EXCLUSIVE

    assert not (CONCURRENCY_SAFE & EXCLUSIVE), "同一个工具不能既安全又独占"
    assert not (CONCURRENCY_SAFE & CONDITIONAL), "conditional 是第三种，不是 safe 的别名"
    assert not (EXCLUSIVE & CONDITIONAL)
    assert set(TOOL_IMPLS) == CONCURRENCY_SAFE | EXCLUSIVE | CONDITIONAL


def test_concurrency_safe_tools_are_read_only_by_name():
    """The safe side must not gain write-class tools (a name-level guard against table moves)."""
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
    # bash is the per-call class: read-only commands parallelize, writes stay exclusive.
    assert "bash" in CONDITIONAL


@pytest.mark.parametrize("item", TOOLS, ids=NAMES)
def test_integer_parameters_declare_a_lower_bound(item):
    """Integer parameters must declare a lower bound: unbounded integers let the model pass 0."""
    for spec in _integer_specs(item["function"]["parameters"]):
        assert isinstance(spec.get("minimum"), int), spec


def test_timeout_parameter_matches_the_enforced_cap():
    """The schema maximum must equal the enforced `shell.MAX_TIMEOUT` clamp (300 s)."""
    from avid.agent.tools import shell

    spec = PARAMETERS["bash"]["properties"]["timeout_seconds"]

    assert spec["minimum"] == 1
    assert spec["maximum"] == shell.MAX_TIMEOUT


def test_expected_tools_are_registered():
    assert sorted(NAMES) == [
        "ask_user",
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
    """`support.PLACEHOLDER_ARGS` must stay schema-valid: a bad entry silently sends loop / svc /
    web cases down the "bad arguments" path."""
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
    """Array parameters must declare their item structure, or the model can only guess."""
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
    """Help lists exactly the registered tools: whitespace stripped because argparse wraps."""
    help_text = "".join(build_parser().format_help().split())

    assert "".join(AGENT_TOOL_HELP.split()) in help_text
    for name in NAMES:
        assert name in help_text, name
