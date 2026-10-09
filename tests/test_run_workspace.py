"""Run-level workspace root: with several workspaces in one process every write must follow
RunState instead of the module-global WORKSPACE_ROOT.

One missed reader yields two answers to "which workspace is this run in" (prompt paths, bash cwd,
file tools, compaction spill), and single-workspace tests cannot see it.
"""

from __future__ import annotations

import pytest

from avid.agent import compaction
from avid.agent.context import ContextManager
from avid.agent.state import RunState
from avid.agent.tools import shell
from avid.agent.tools.files import read_file, write_file
from avid.agent.transcript import Transcript
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def system_of(state):
    return ContextManager(
        transcript=Transcript([{"role": "user", "content": "x"}]),
        state=state,
        config=CONFIG,
    ).compose().system


@pytest.fixture
def other(tmp_path):
    """A second workspace, outside ``sandbox``: a prefix relationship would make the "path
    inside?" assertions pass vacuously."""
    path = tmp_path.parent / f"second-{tmp_path.name}"
    path.mkdir()
    return path


def test_bash_runs_in_the_run_root(sandbox, other):
    state = RunState.for_run(workspace_root=str(other))

    result = shell.bash({"command": "pwd"}, state=state)

    assert str(other.resolve()) in result
    assert str(sandbox.resolve()) not in result


def test_file_tools_resolve_against_the_run_root(sandbox, other):
    state = RunState.for_run(workspace_root=str(other))

    write_file({"path": "notes.txt", "content": "在第二个工作区"}, state=state)

    assert (other / "notes.txt").read_text(encoding="utf-8") == "在第二个工作区"
    assert not (sandbox / "notes.txt").exists()
    assert read_file({"path": "notes.txt"}, state=state) == "在第二个工作区"


def test_environment_block_reports_the_run_root(sandbox, other):
    state = RunState.for_run(workspace_root=str(other))

    system = system_of(state)

    assert f"工作目录：{other}\n" in system
    assert f"工作目录：{sandbox}\n" not in system


def test_compaction_spills_into_the_run_root(sandbox, other):
    path = compaction._save_transcript([{"role": "user", "content": "x"}], other)

    assert path.startswith(f"{compaction.SPILL_DIR}/")
    assert (other / compaction.SPILL_DIR).is_dir()
    assert not (sandbox / compaction.SPILL_DIR).exists()


def test_without_a_run_root_everything_falls_back_to_the_process_root(sandbox):
    """Without a run root everything falls back to the process root (single-workspace path)."""
    state = RunState.for_run()

    write_file({"path": "fallback.txt", "content": "默认根"}, state=state)
    system = system_of(state)

    assert (sandbox / "fallback.txt").exists()
    assert f"工作目录：{sandbox}" in system
