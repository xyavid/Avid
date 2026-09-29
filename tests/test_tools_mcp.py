"""stdio MCP 客户端与它的权限闸门（阶段 30e）。

用一个**真的子进程**当假 server（tests/support/fake_mcp_server.py，同一份协议实现
跑在两条测试里）：握手、列举、调用、isError、超时、进程清理都是真实的——stub 掉
stdio 协议层只会测到我们自己写的 mock。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from avid.policy.action import brokerize
from avid.policy.engine import decide
from avid.policy.permission import ApprovalLedger
from avid.tools.mcp import McpManager, mcp_config_path

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


# ---------------- 配置发现与进程生命周期 ----------------


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


# ---------------- 权限闸门 ----------------


def _action_args():
    return {"text": "hi"}


def test_manual_mode_asks_once_per_tool_then_ledger_reuses(workspace):
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    ledger = ApprovalLedger()
    asked: list[str] = []

    def ask(tool, arguments, reason):
        asked.append(tool)
        return True

    first = decide(
        brokerize("mcp__demo__echo", _action_args()),
        mode="manual",
        ledger=ledger,
        ask=ask,
    )
    second = decide(
        brokerize("mcp__demo__echo", _action_args()),
        mode="manual",
        ledger=ledger,
        ask=ask,
    )

    assert first.verdict == "allow" and first.answered_by == "user"
    assert second.verdict == "allow" and second.answered_by == "ledger"
    assert asked == ["mcp__demo__echo"]
    manager.close()


def test_full_mode_allows_without_asking(workspace):
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    decision = decide(
        brokerize("mcp__demo__echo", _action_args()),
        mode="full",
        ledger=ApprovalLedger(),
        ask=lambda *a: pytest.fail("full 不该问人"),
    )

    assert decision.verdict == "allow"
    manager.close()


def test_auto_mode_denies_unreadable_mcp_tools(workspace):
    """分类器看不见 MCP 工具的语义：auto 判不准即拒，而不是放行。"""
    write_config(workspace, CONFIG_OK)
    manager = McpManager(str(workspace))
    manager.start_all()

    decision = decide(
        brokerize("mcp__demo__echo", _action_args()),
        mode="auto",
        ledger=ApprovalLedger(),
        ask=lambda *a: pytest.fail("auto 不问人"),
    )

    assert decision.verdict == "deny"
    assert "mcp" in decision.reason.lower() or "MCP" in decision.reason
    manager.close()
