"""The assembly's own contract: what it must do when its own steps fail.

`assemble()` holds the only reference to the run's resources while it builds them, so a failure in
any of its steps — from starting a server to resolving the spec — is the one moment nobody else can
clean up. That is the whole subject here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from avid.agent.tools import TOOLS
from avid.providers.config import Config
from avid.services import assembly as assembly_module

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")
USER = [{"role": "user", "content": "x"}]


class StubMcp:
    """An MCP manager that records being closed; start_all's answer is injectable."""

    def __init__(self, root: str | None, *, warnings: list[str] | None = None, raises=None) -> None:
        self.root = root
        self.closed = False
        self._warnings = list(warnings or [])
        self._raises = raises

    def start_all(self) -> list[str]:
        if self._raises is not None:
            raise self._raises
        return list(self._warnings)

    def toolset(self) -> tuple[list, dict]:
        return [], {}

    def close(self) -> None:
        self.closed = True


def _boom(*args, **kwargs):
    raise RuntimeError("装配失败")


#: Every exit between `state.mcp = manager` and the return. One missing entry here is one way a
#: started server outlives the run it was started for.
FAILURE_EXITS = ("start_all", "warn", "build_toolset", "resolve")


@pytest.mark.parametrize("exit_name", FAILURE_EXITS)
def test_any_failure_once_servers_may_have_started_closes_them(monkeypatch, tmp_path, exit_name):
    """The caller has no state to close when assembly raises, so the closure must happen inside."""
    started: list[StubMcp] = []

    def factory(root: str | None, *, config_path) -> StubMcp:
        manager = StubMcp(
            root,
            warnings=["一条启动警告"],
            raises=RuntimeError("装配失败") if exit_name == "start_all" else None,
        )
        started.append(manager)
        return manager

    monkeypatch.setattr(assembly_module, "McpManager", factory)
    if exit_name == "build_toolset":
        monkeypatch.setattr(assembly_module, "build_toolset", _boom)
    elif exit_name == "resolve":
        monkeypatch.setattr(assembly_module, "RunSpec", SimpleNamespace(resolve=_boom))

    def warn(message: str) -> None:
        if exit_name == "warn":
            raise RuntimeError("装配失败")

    with pytest.raises(RuntimeError, match="装配失败"):
        assembly_module.assemble(
            config=CONFIG, messages=list(USER), workspace_root=str(tmp_path), warn=warn
        )

    assert [manager.closed for manager in started] == [True], exit_name


def test_tools_and_registry_are_given_together(tmp_path):
    """Half an injected table is worse than none: tools without handlers answers 未知工具 to every
    call, and handlers without tools are unreachable. Refuse it where it is decided.
    """
    with pytest.raises(ValueError, match="registry"):
        assembly_module.assemble(
            config=CONFIG, messages=list(USER), workspace_root=str(tmp_path), tools=TOOLS
        )
    with pytest.raises(ValueError, match="registry"):
        assembly_module.assemble(
            config=CONFIG, messages=list(USER), workspace_root=str(tmp_path), registry={}
        )
