import pytest

from avid.tools import shell, workspace
from avid.tools.shell import bash


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


def test_bash_runs_in_workspace_root(sandbox):
    (sandbox / "marker.txt").write_text("x", encoding="utf-8")

    result = bash({"command": "ls"})

    assert "marker.txt" in result
    assert "[exit 0]" in result


def test_bash_reports_non_zero_exit(sandbox):
    assert "[exit 3]" in bash({"command": "exit 3"})


def test_bash_merges_stderr(sandbox):
    result = bash({"command": "echo out; echo err 1>&2"})

    assert "out" in result
    assert "[stderr]" in result
    assert "err" in result


def test_bash_allows_cd_inside_one_command(sandbox):
    (sandbox / "sub").mkdir()

    assert "sub" in bash({"command": "cd sub && pwd"})


def test_bash_times_out(sandbox):
    assert "超时" in bash({"command": "sleep 5", "timeout_seconds": 1})


def test_bash_truncates_output(sandbox, monkeypatch):
    monkeypatch.setattr(shell, "MAX_OUTPUT_CHARS", 50)

    result = bash({"command": "printf 'a%.0s' {1..500}"})

    assert "输出已截断" in result


def test_bash_requires_command(sandbox):
    assert "command" in bash({"command": "   "})


def test_timeout_is_clamped_and_tolerant():
    assert shell._timeout(9999) == shell.MAX_TIMEOUT
    assert shell._timeout(0) == 1
    assert shell._timeout("abc") == shell.DEFAULT_TIMEOUT
    assert shell._timeout(None) == shell.DEFAULT_TIMEOUT
