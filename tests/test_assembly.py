"""The assembly's own contract: what it must do when its own steps fail.

`assemble()` holds the only reference to the run's resources while it builds them, so a failure in
any of its later steps is the one moment nobody else can clean up. That is the whole subject here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from avid.agent.tools import TOOLS
from avid.providers.config import Config
from avid.services import assembly as assembly_module

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


class StubMcp:
    """An MCP manager that starts nothing and records being closed."""

    def __init__(self, root: str | None) -> None:
        self.root = root
        self.closed = False

    def start_all(self) -> list[str]:
        return []

    def toolset(self) -> tuple[list, dict]:
        return [], {}

    def close(self) -> None:
        self.closed = True


def test_a_failure_after_the_servers_start_still_closes_them(monkeypatch, tmp_path):
    """A raise between starting MCP and returning means the run never exists: the servers must
    not outlive it, and the caller has no state to close.
    """
    started: list[StubMcp] = []

    def stub(root: str | None) -> StubMcp:
        manager = StubMcp(root)
        started.append(manager)
        return manager

    def boom(**kwargs):
        raise RuntimeError("resolve 失败")

    monkeypatch.setattr(assembly_module, "McpManager", stub)
    monkeypatch.setattr(assembly_module, "RunSpec", SimpleNamespace(resolve=boom))

    with pytest.raises(RuntimeError, match="resolve 失败"):
        assembly_module.assemble(
            config=CONFIG,
            messages=[{"role": "user", "content": "x"}],
            workspace_root=str(tmp_path),
        )

    assert [manager.closed for manager in started] == [True]


def test_tools_and_registry_are_given_together(tmp_path):
    """Half an injected table is worse than none: tools without handlers answers 未知工具 to every
    call, and handlers without tools are unreachable. Refuse it where it is decided.
    """
    with pytest.raises(ValueError, match="registry"):
        assembly_module.assemble(
            config=CONFIG,
            messages=[{"role": "user", "content": "x"}],
            workspace_root=str(tmp_path),
            tools=TOOLS,
        )
    with pytest.raises(ValueError, match="registry"):
        assembly_module.assemble(
            config=CONFIG,
            messages=[{"role": "user", "content": "x"}],
            workspace_root=str(tmp_path),
            registry={},
        )
