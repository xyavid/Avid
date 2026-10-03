"""Host-side folder picker that opens a native dialog on the host desktop, loopback only."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress

logger = logging.getLogger("avid.svc.picker")

# Names an explicit picker command in the environment; also the deterministic seam used by tests.
ENV_OVERRIDE = "AVID_PICKER_CMD"

# Seconds a dialog may stay open before it counts as cancelled, so a request cannot hang.
PICKER_TIMEOUT_SECONDS = 300.0

# Caption shown inside the dialog window.
TITLE = "选择工作区文件夹"


class PickerError(Exception):
    """Picker failure whose message is written for the user and can be shown directly."""


class PickerUnavailable(PickerError):
    """No backend is available on this machine."""


class PickerFailed(PickerError):
    """A backend is available but its execution failed."""


class _BackendUnavailable(Exception):
    """Internal signal that this backend cannot start here, so the next one is tried."""


# One backend per supported environment; each returns a path or None on cancellation.

def _finish(result: subprocess.CompletedProcess[str]) -> str | None:
    """Turn a process result into a path or a cancellation; non-zero exit codes count as cancel."""

    if result.returncode != 0:
        # A non-zero exit code means the user cancelled, which must not be reported as a failure.
        if result.stderr.strip():
            logger.info("选择器退出码 %s：%s", result.returncode, result.stderr.strip())
        return None
    text = result.stdout.strip()
    return text or None


def _override(timeout: float, env: dict[str, str]) -> str | None:
    """Run the command named by the environment override and take its stdout as the chosen path."""
    command = env.get(ENV_OVERRIDE, "").strip()
    if not command:
        raise _BackendUnavailable("未设置 AVID_PICKER_CMD")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise PickerFailed(f"AVID_PICKER_CMD 解析失败：{exc}") from exc
    if not argv:
        raise _BackendUnavailable("AVID_PICKER_CMD 为空")
    return _finish(_run(argv, timeout))


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one picker process, reporting timeouts and spawn failures as picker failures."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # A timeout means the backend started but produced nothing, so it must not escape as a 500.
        raise PickerFailed(f"选择器 {argv[0]} 超时（{timeout:.0f} 秒）") from exc
    except OSError as exc:
        raise PickerFailed(f"选择器 {argv[0]} 起不来：{exc}") from exc


def _tkinter(timeout: float, env: dict[str, str]) -> str | None:
    """Open a Tk dialog in-process; the timeout closes the window, which counts as a cancel."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:  # pragma: no cover - depends on the distribution
        raise _BackendUnavailable(f"tkinter 不可用（{exc}）") from exc

    try:
        root = tk.Tk()
    except Exception as exc:  # TclError: no display is reachable
        raise _BackendUnavailable(f"连不上显示（{exc}）") from exc

    root.withdraw()
    root.after(int(timeout * 1000), root.destroy)
    try:
        chosen = filedialog.askdirectory(parent=root, title=TITLE, mustexist=True)
    except Exception as exc:  # also reached when the timer closed the window
        logger.info("tkinter 对话框异常（多半是超时关闭）：%s", exc)
        return None
    finally:
        with suppress(Exception):  # already destroyed
            root.destroy()
    return chosen or None


def _zenity(timeout: float, env: dict[str, str]) -> str | None:
    binary = shutil.which("zenity")
    if binary is None:
        raise _BackendUnavailable("没有 zenity")
    return _finish(
        _run([binary, "--file-selection", "--directory", f"--title={TITLE}"], timeout)
    )


def _kdialog(timeout: float, env: dict[str, str]) -> str | None:
    binary = shutil.which("kdialog")
    if binary is None:
        raise _BackendUnavailable("没有 kdialog")
    return _finish(_run([binary, "--getexistingdirectory", os.getcwd()], timeout))


# PowerShell fragment that shows the native folder browser and prints the chosen path.
_POWERSHELL_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f"$d.Description = '{TITLE}'; "
    "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ Write-Output $d.SelectedPath }"
)


def _to_local_path(raw: str) -> str:
    """Translate a Windows path to a local one, accepting both drive letters and UNC shares."""
    translator = shutil.which("wslpath")
    if translator is None:
        return raw
    result = _run([translator, "-u", raw], 10.0)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else raw


def _windows(timeout: float, env: dict[str, str]) -> str | None:
    binary = shutil.which("powershell.exe")
    if binary is None:
        raise _BackendUnavailable("没有 powershell.exe（不在 WSL 或未开互操作）")
    result = _run(
        [binary, "-NoProfile", "-STA", "-Command", _POWERSHELL_SCRIPT], timeout
    )
    raw = _finish(result)
    return None if raw is None else _to_local_path(raw)


def _osascript(timeout: float, env: dict[str, str]) -> str | None:
    binary = shutil.which("osascript")
    if binary is None:
        raise _BackendUnavailable("没有 osascript")
    return _finish(
        _run(
            [
                binary,
                "-e",
                f'POSIX path of (choose folder with prompt "{TITLE}")',
            ],
            timeout,
        )
    )


# Order is priority: in-process backends first, then desktop tools, then the OS bridges.
BACKENDS: tuple[tuple[str, Callable[[float, dict[str, str]], str | None]], ...] = (
    ("override", _override),
    ("tkinter", _tkinter),
    ("zenity", _zenity),
    ("kdialog", _kdialog),
    ("windows", _windows),
    ("osascript", _osascript),
)

if sys.platform == "darwin":  # Tk must run on the main thread on macOS, so skip it there
    BACKENDS = tuple(item for item in BACKENDS if item[0] != "tkinter")


def pick_directory(
    *,
    timeout: float = PICKER_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> str | None:
    """Open one folder dialog; returns an absolute path, None on cancel, and raises with none."""
    environment = dict(os.environ if env is None else env)
    # Reasons from every backend that could not start, quoted in the error message.
    problems: list[str] = []

    for name, backend in BACKENDS:
        try:
            chosen = backend(timeout, environment)
        except _BackendUnavailable as exc:
            problems.append(f"{name}: {exc}")
            continue
        header = "已选择" if chosen else "已取消"
        logger.info("文件夹选择器（%s）：%s", name, header)
        return chosen

    detail = "；".join(problems) or "没有任何后端"
    raise PickerUnavailable(
        f"这台机器上没有可用的系统文件夹选择器（{detail}）。"
        "可以直接在命令行登记：`avid workspace add <文件夹路径>`"
    )


# Diagnostic cache lifetime; short so installing a backend or changing the override shows up soon.
_BACKEND_TTL_SECONDS = 30.0
# Cached probe result as a (monotonic time, backend name) pair, or None before the first probe.
_backend_cache: tuple[float, str | None] | None = None


def _probe_backend() -> str | None:
    """Return the first backend that could run here, without opening any dialog."""
    environment = dict(os.environ)
    for name, _backend in BACKENDS:
        if name == "override":
            if environment.get(ENV_OVERRIDE, "").strip():
                return "override"
            continue
        if name == "tkinter":
            try:
                import tkinter  # noqa: F401
                from tkinter import Tcl  # noqa: F401
            except ImportError:
                continue
            # tkinter also needs a reachable display to be usable.
            if not (environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY")):
                continue
            return "tkinter"
        # Executable each remaining backend needs to be on PATH.
        binary = {
            "zenity": "zenity",
            "kdialog": "kdialog",
            "windows": "powershell.exe",
            "osascript": "osascript",
        }.get(name)
        if binary and shutil.which(binary):
            return name
    return None


def available_backend() -> str | None:
    """Name of the backend that would be used, cached briefly for the capability report."""
    global _backend_cache
    now = time.monotonic()
    if _backend_cache is not None and now - _backend_cache[0] < _BACKEND_TTL_SECONDS:
        return _backend_cache[1]
    found = _probe_backend()
    _backend_cache = (now, found)
    return found


def clear_backend_cache() -> None:
    """Drop the diagnostic cache so the next probe re-reads the environment."""
    global _backend_cache
    _backend_cache = None


__all__ = [
    "BACKENDS",
    "clear_backend_cache",
    "ENV_OVERRIDE",
    "PICKER_TIMEOUT_SECONDS",
    "PickerError",
    "PickerFailed",
    "PickerUnavailable",
    "available_backend",
    "pick_directory",
]
