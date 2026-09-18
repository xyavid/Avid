"""系统文件夹选择器：由**本机后端**弹原生对话框，返回绝对路径。

浏览器拿不到目录的绝对路径——``<input webkitdirectory>`` 只给相对路径，File System
Access API 只给一个 handle——所以"选文件夹"这件事只能由跑在本机的后端做，界面负责
发起与展示结果。

后端按顺序探测，第一个能起来的胜出：

1. ``AVID_PICKER_CMD``（显式覆盖：一条命令把选中的路径打到 stdout；也用作 e2e 的确定性接缝）
2. **tkinter**（跨平台，WSLg / Linux 桌面 / Windows 原生都能用；返回本机路径，无需翻译）
3. ``zenity`` / ``kdialog``（Linux 桌面常见）
4. **Windows 原生对话框**（WSL 互操作调 ``powershell.exe``，路径用 ``wslpath -u`` 翻译回来）
5. ``osascript``（macOS）

约定（决定调用方怎么处理结果）：

* **用户取消 → 返回 ``None``**，不是错误。"取消"与"失败"必须分得开，否则界面会把
  一次取消报成故障；
* **没有可用后端 → :class:`PickerUnavailable`**，调用方据此给出可执行的替代做法
  （``avid workspace add <路径>``）；
* **后端起得来但失败 → :class:`PickerFailed`**（带后端名与原因）；
* 一次只允许一个对话框（由调用方持锁）：第二个窗口会盖住第一个，用户会以为卡死。

**只应在回环地址上暴露。** 这个能力等于"让服务进程在宿主机桌面上弹窗"，与审批按钮
是同一条理由（``AGENTS.md`` 第 6 节）。
"""

from __future__ import annotations

import functools
import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable

logger = logging.getLogger("avid.svc.picker")

ENV_OVERRIDE = "AVID_PICKER_CMD"

# 对话框是给人看的：默认给 5 分钟，超时按"取消"处理（关掉窗口），免得 HTTP 请求挂着。
PICKER_TIMEOUT_SECONDS = 300.0

TITLE = "选择工作区文件夹"


class PickerError(Exception):
    """选择器失败。消息面向使用者，可直接展示。"""


class PickerUnavailable(PickerError):
    """没有任何可用的后端。"""


class PickerFailed(PickerError):
    """后端可用但执行失败。"""


class _BackendUnavailable(Exception):
    """这个后端在这台机器上起不来——试下一个（内部信号，不外传）。"""


# ---------------- 后端 ----------------

def _finish(result: subprocess.CompletedProcess[str]) -> str | None:
    """把一次子进程调用收敛成"路径 / 取消"。

    **非零退出码一律按取消处理**：zenity 与 kdialog 都用 1 表示取消，而把非零当失败
    会把一次正常的取消报成故障。
    """
    if result.returncode != 0:
        if result.stderr.strip():
            logger.info("选择器退出码 %s：%s", result.returncode, result.stderr.strip())
        return None
    text = result.stdout.strip()
    return text or None


def _override(timeout: float, env: dict[str, str]) -> str | None:
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
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _tkinter(timeout: float, env: dict[str, str]) -> str | None:
    """进程内弹 Tk 对话框。

    超时用 ``after`` 关掉窗口（等价于取消），所以它不会比 ``timeout`` 更久。
    macOS 要求 Tk 在主线程——那里请用 ``AVID_PICKER_CMD`` 调 ``osascript``，
    这里连不上就自动落到下一个后端。
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:  # pragma: no cover - 取决于发行版
        raise _BackendUnavailable(f"tkinter 不可用（{exc}）") from exc

    try:
        root = tk.Tk()
    except Exception as exc:  # TclError：连不上显示（无 X / 无 WSLg）
        raise _BackendUnavailable(f"连不上显示（{exc}）") from exc

    root.withdraw()
    root.after(int(timeout * 1000), root.destroy)
    try:
        chosen = filedialog.askdirectory(parent=root, title=TITLE, mustexist=True)
    except Exception as exc:  # 窗口被 after 关掉时也会走到这里
        logger.info("tkinter 对话框异常（多半是超时关闭）：%s", exc)
        return None
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001 - 已销毁
            pass
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


_POWERSHELL_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f"$d.Description = '{TITLE}'; "
    "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ Write-Output $d.SelectedPath }"
)


def _to_local_path(raw: str) -> str:
    """Windows 路径 → 本机路径。``wslpath`` 认 ``C:\\…`` 与 ``\\\\wsl.localhost\\…``。"""
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


# 顺序即优先级：先本机进程内（无需翻译路径），再到桌面工具，最后到 Windows/macOS 的桥。
BACKENDS: tuple[tuple[str, Callable[[float, dict[str, str]], str | None]], ...] = (
    ("override", _override),
    ("tkinter", _tkinter),
    ("zenity", _zenity),
    ("kdialog", _kdialog),
    ("windows", _windows),
    ("osascript", _osascript),
)

if sys.platform == "darwin":  # macOS 上 Tk 要主线程，别在请求线程里试
    BACKENDS = tuple(item for item in BACKENDS if item[0] != "tkinter")


def pick_directory(
    *,
    timeout: float = PICKER_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> str | None:
    """弹一次文件夹选择器。返回绝对路径；用户取消返回 ``None``。"""
    environment = dict(os.environ if env is None else env)
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


@functools.lru_cache(maxsize=1)
def available_backend() -> str | None:
    """第一个能起来的后端名（只探测，不弹窗）。给 `/api/meta` 做诊断用。"""
    environment = dict(os.environ)
    for name, backend in BACKENDS:
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
            if not (environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY")):
                continue
            return "tkinter"
        if name == "zenity":
            if shutil.which("zenity"):
                return name
        elif name == "kdialog":
            if shutil.which("kdialog"):
                return name
        elif name == "windows":
            if shutil.which("powershell.exe"):
                return name
        elif name == "osascript":
            if shutil.which("osascript"):
                return name
    return None


__all__ = [
    "BACKENDS",
    "ENV_OVERRIDE",
    "PICKER_TIMEOUT_SECONDS",
    "PickerError",
    "PickerFailed",
    "PickerUnavailable",
    "available_backend",
    "pick_directory",
]
