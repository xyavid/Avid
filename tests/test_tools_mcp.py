"""stdio MCP client and its decision path.

Tests drive a real subprocess server (tests/support/fake_mcp_server.py), so handshake,
listing, calls, isError, timeouts, and process cleanup are all real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from avid.agent.tools.mcp import McpManager, mcp_config_path
from avid.security.action import brokerize
from avid.security.engine import decide
from avid.security.permission import ApprovalLedger

SERVER = [sys.executable, str(Path(__file__).parent / "support" / "fake_mcp_server.py")]

CONFIG_OK = {"servers": [{"name": "demo", "command": SERVER[0], "args": [SERVER[1]]}]}
CONFIG_BAD_SCRIPT = {
    "servers": [{"name": "dead", "command": SERVER[0], "args": [SERVER[1], "--crash"]}]
}


def write_config(workspace: Path, payload) -> Path:
    path = mcp_config_path(str(workspace))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


# ---- config discovery and process lifecycle ----


def test_no_config_means_empty_toolset(workspace):
    manager = McpManager(str(workspace))
    warnings = manager.start_all()

    assert warnings == []
    assert manager.toolset() == ([], {})


def test_malformed_config_is_skipped_with_a_warning(workspace):
    path = mcp_config_path(str(workspace))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{不是 JSON", encoding="utf-8")

    manager = McpManager(str(workspace))
    warnings = manager.start_all()

    assert len(warnings) == 1 and "mcp.json" in warnings[0]
    assert manager.toolset() == ([], {})


def test_toolset_exposes_schema_and_working_impl(workspace):
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    assert manager.start_all() == []

    schemas, impls = manager.toolset()
    assert [item["function"]["name"] for item in schemas] == ["mcp__demo__echo"]

    schema = schemas[0]["function"]
    assert schema["description"]
    assert schema["parameters"]["type"] == "object"

    result = impls["mcp__demo__echo"]({"text": "hi"})
    assert result == "echo:hi"

    manager.close()
    assert manager.processes_alive() == 0


def test_dead_server_becomes_a_warning_not_a_failure(workspace):
    write_config(workspace, CONFIG_BAD_SCRIPT)
    manager = McpManager(str(workspace))
    warnings = manager.start_all()

    assert len(warnings) == 1 and "dead" in warnings[0]
    assert manager.toolset() == ([], {})
    manager.close()


def test_call_timeout_returns_error_text(workspace):
    write_config(
        workspace,
        {
            "servers": [
                {
                    "name": "slow",
                    "command": SERVER[0],
                    "args": [SERVER[1], "--delay", "5"],
                    "timeout_seconds": 0.2,
                }
            ]
        },
    )
    manager = McpManager(str(workspace))
    assert manager.start_all() == []

    _, impls = manager.toolset()
    result = impls["mcp__slow__echo"]({"text": "hi"})

    assert result.startswith("错误：")
    assert "超时" in result
    manager.close()


def test_iserror_response_becomes_error_text(workspace):
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    _, impls = manager.toolset()
    result = impls["mcp__demo__echo"]({"text": "boom", "fail": True})

    assert result.startswith("错误：")
    manager.close()


def test_close_terminates_servers(workspace):
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()
    assert manager.processes_alive() == 1

    manager.close()
    assert manager.processes_alive() == 0


# ---- decisions: MCP executes directly and never asks ----


def _action_args():
    return {"text": "hi"}


def test_mcp_tools_execute_without_asking_or_using_the_ledger(workspace):
    """MCP tools execute directly: no asking, no ledger entry, and repeated calls get the
    same answer."""
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    ledger = ApprovalLedger()
    asked: list[str] = []

    def ask(tool, arguments, reason):
        asked.append(tool)
        return True

    first = decide(brokerize("mcp__demo__echo", _action_args()), ledger=ledger, ask=ask)
    second = decide(brokerize("mcp__demo__echo", _action_args()), ledger=ledger, ask=ask)

    assert first.verdict == "allow" and first.answered_by == "policy"
    assert second.verdict == "allow" and second.answered_by == "policy"
    assert asked == [], "MCP 工具不该经过询问通道"
    assert len(ledger) == 0, "直接执行不产生账本条目"
    manager.close()


def test_full_run_allows_mcp_without_asking(workspace):
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    decision = decide(
        brokerize("mcp__demo__echo", _action_args()),
        full=True,
        ledger=ApprovalLedger(),
        ask=lambda *a: pytest.fail("MCP 工具不该问人"),
    )

    assert decision.verdict == "allow"
    assert decision.answered_by == "policy"
    manager.close()


def test_mcp_tools_are_not_questioned_even_without_an_answerer(workspace):
    """MCP semantics cannot be classified statically, but MCP tools never ask a human:
    they execute whether or not an answerer exists."""
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    asked = []
    allowed = decide(
        brokerize("mcp__demo__echo", _action_args()),
        ledger=ApprovalLedger(),
        ask=lambda *a: asked.append(a) or True,
    )
    assert allowed.allowed and allowed.answered_by == "policy"
    assert asked == []

    refused = decide(brokerize("mcp__demo__echo", _action_args()), ledger=ApprovalLedger())
    assert refused.allowed and refused.answered_by == "policy"
    manager.close()
