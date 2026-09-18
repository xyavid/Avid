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


def test_bash_bounds_a_flooding_command(sandbox):
    """输出上限必须在**读的时候**生效：以前 capture_output 会把整个输出读进内存。

    `yes` 是无尽的输出；超过"上限 × 余量"就判定为刷屏并终止命令，而不是一路读完。
    """
    import time

    started = time.perf_counter()
    result = bash({"command": "yes"})
    elapsed = time.perf_counter() - started

    assert "输出过多已终止命令" in result
    assert elapsed < 10, f"刷屏命令没有被及时终止（{elapsed:.1f}s）"


def test_bash_timeout_kills_the_whole_process_group(sandbox):
    """超时要连子孙一起清理：只 kill 直接子进程时，后台子壳会活下来继续干活。

    命令在后台立刻起一个"1.5 秒后写标记"的子壳，超时设 1 秒。若只杀 bash 自己，
    那个子壳会在 1.5 秒时把标记写出来——这正是 `bash -c 'npm run …'` 留下的
    一堆孤儿进程。
    """
    import time

    result = bash(
        {
            "command": "(sleep 1.5; touch late-marker) & sleep 30",
            "timeout_seconds": 1,
        }
    )
    assert "超时" in result

    time.sleep(1.2)  # 越过子壳原本要写标记的时刻
    assert not (sandbox / "late-marker").exists(), "子进程活下来了：超时没清进程组"
