"""运行级工作区根：一个进程服务多个工作区时，所有落点都必须跟着 state 走。

这是阶段 18 风险最高的一处重构——模块全局 ``WORKSPACE_ROOT`` 有 9 个读取点，
漏掉任何一个都会让"这次运行在哪个工作区"出现两套答案（模型看到的路径、bash 的
cwd、文件工具、任务库、压缩落盘可能各说各话），而单工作区的测试发现不了。
"""

from __future__ import annotations

import pytest

from avid.policy import compaction
from avid.runtime.state import RunState
from avid.tools import shell, tasks
from avid.tools.files import read_file, write_file


@pytest.fixture
def other(tmp_path):
    """第二块工作区：与 sandbox（进程默认根）不同，且**不在它里面**——
    否则 str(sandbox) 是 str(other) 的前缀，"路径含不含"这类断言全是假绿。"""
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


def test_task_store_follows_the_run_root(sandbox, other):
    state = RunState.for_run(workspace_root=str(other))

    tasks.create_task_tool({"subject": "另一个工作区的任务"}, state=state)

    assert (other / ".tasks").is_dir()
    assert not (sandbox / ".tasks").exists()


def test_system_prompt_reports_the_run_root(sandbox, other):
    state = RunState.for_run(workspace_root=str(other))

    prompt = state.system_prompt()

    assert f"工作目录：{other}\n" in prompt
    assert f"工作目录：{sandbox}\n" not in prompt


def test_compaction_spills_into_the_run_root(sandbox, other):
    path = compaction._save_transcript([{"role": "user", "content": "x"}], other)

    assert path.startswith(f"{compaction.SPILL_DIR}/")
    assert (other / compaction.SPILL_DIR).is_dir()
    assert not (sandbox / compaction.SPILL_DIR).exists()


def test_without_a_run_root_everything_falls_back_to_the_process_root(sandbox):
    """没有运行级根时行为与改动前一致——单工作区路径不受影响。"""
    state = RunState.for_run()

    write_file({"path": "fallback.txt", "content": "默认根"}, state=state)
    tasks.create_task_tool({"subject": "默认根的任务"}, state=state)
    prompt = state.system_prompt()

    assert (sandbox / "fallback.txt").exists()
    assert (sandbox / ".tasks").is_dir()
    assert f"工作目录：{sandbox}" in prompt
