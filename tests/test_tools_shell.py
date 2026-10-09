import pytest

from avid.agent.tools import shell, workspace
from avid.agent.tools.shell import bash


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


def test_bash_truncation_keeps_the_tail_and_the_exit_code(sandbox, monkeypatch):
    """Truncation keeps the tail and the exit code: early output is least informative, and
    `[exit N]` must survive or failure looks like truncation."""
    monkeypatch.setattr(shell, "MAX_OUTPUT_CHARS", 200)

    result = bash(
        {
            "command": (
                "printf 'EARLY-MARK'; printf 'HEAD-%.0s' {1..200}; printf 'TAIL-MARK'; exit 7"
            )
        }
    )

    assert "TAIL-MARK" in result
    assert "[exit 7]" in result
    assert "输出已截断" in result
    assert "EARLY-MARK" not in result


def test_bash_requires_command(sandbox):
    assert "command" in bash({"command": "   "})


def test_timeout_is_clamped_and_tolerant():
    assert shell._timeout(9999) == shell.MAX_TIMEOUT
    assert shell._timeout(0) == 1
    assert shell._timeout("abc") == shell.DEFAULT_TIMEOUT
    assert shell._timeout(None) == shell.DEFAULT_TIMEOUT


def test_bash_bounds_a_flooding_command(sandbox):
    """The output cap applies while reading: a command flooding past cap x margin is killed,
    not read to the end."""
    import time

    started = time.perf_counter()
    result = bash({"command": "yes"})
    elapsed = time.perf_counter() - started

    assert "输出过多已终止命令" in result
    assert elapsed < 10, f"刷屏命令没有被及时终止（{elapsed:.1f}s）"


def test_bash_timeout_kills_the_whole_process_group(sandbox):
    """A timeout kills the whole process group: killing only `bash` leaves the background
    subshell alive to keep working."""
    import time

    result = bash(
        {
            "command": "(sleep 1.5; touch late-marker) & sleep 30",
            "timeout_seconds": 1,
        }
    )
    assert "超时" in result

    time.sleep(1.2)  # past the moment the subshell was to write the marker
    assert not (sandbox / "late-marker").exists(), "子进程活下来了：超时没清进程组"


# ---- platform adaptation ----


def test_shell_argv_selects_the_interpreter_per_platform():
    import base64

    import avid.agent.tools.shell as shell_module
    from avid.agent.tools.shell import shell_argv

    assert shell_argv("ls", platform="linux")[0].endswith("bash")
    assert shell_argv("ls", platform="linux")[1:] == ["-c", "ls"]

    windows = shell_argv("Get-Date", platform="win32")
    assert windows[0].endswith(("pwsh", "pwsh.exe", "powershell", "powershell.exe"))
    assert "-NoProfile" in windows and "-NonInteractive" in windows
    # -EncodedCommand (UTF-16LE base64) bypasses quoting; the prefix forces UTF-8 output
    assert windows[-2] == "-EncodedCommand"
    script = base64.b64decode(windows[-1]).decode("utf-16-le")
    assert script.startswith(shell_module._UTF8_PREFIX)
    assert script.endswith("Get-Date")


def test_shell_argv_reports_a_missing_powershell(monkeypatch):
    import pytest

    import avid.agent.tools.shell as shell_module
    from avid.agent.tools.shell import ShellUnavailableError, shell_argv

    monkeypatch.setattr(shell_module.shutil, "which", lambda name: None)
    with pytest.raises(ShellUnavailableError):
        shell_argv("Get-Date", platform="win32")


def test_kill_tree_uses_taskkill_on_windows(monkeypatch):
    from types import SimpleNamespace

    import avid.agent.tools.shell as shell_module
    from avid.agent.tools.shell import kill_tree

    calls: list[list[str]] = []
    monkeypatch.setattr(
        shell_module.subprocess, "run", lambda argv, **kwargs: calls.append(argv)
    )
    kill_tree(SimpleNamespace(pid=4321), platform="win32")
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_kill_tree_posix_path_kills_the_process_group(monkeypatch):
    import avid.agent.tools.shell as shell_module
    from avid.agent.tools.shell import kill_tree

    seen: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 777

    def fake_getpgid(pid):
        return 700 + pid

    def fake_killpg(pgid, sig):
        seen.append((pgid, sig))

    monkeypatch.setattr(shell_module.os, "getpgid", fake_getpgid)
    monkeypatch.setattr(shell_module.os, "killpg", fake_killpg)
    kill_tree(FakeProcess(), platform="linux")
    assert seen == [(700 + 777, shell_module.signal.SIGKILL)]
