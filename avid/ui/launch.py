"""One command that brings the UI up: find the build, make sure an API answers, serve it, open it.

This is the piece a desktop shell would otherwise duplicate — start a child, wait for health, open a
window, take the child down on the way out — which is why it is worth having before any shell.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from .browser import open_window as open_app_window
from .browser import resolve_command
from .server import serve

logger = logging.getLogger("avid.ui.launch")

DEFAULT_API_PORT = 8765
#: Fixed rather than chosen per run: a PWA's identity is its origin, so a moving port would make an
#: installed shortcut point at nothing.
DEFAULT_UI_PORT = 4173
DIST_ENV = "AVID_UI_DIST"
#: Duplicated from `avid.web.app` deliberately: importing that module would pull FastAPI into a
#: CLI-only install. `tests/test_ui_launcher.py` asserts the two strings still agree.
ALLOWED_ORIGINS_ENV = "AVID_ALLOWED_ORIGINS"
#: How long the child gets to answer /api/health before the launch is called failed.
HEALTH_TIMEOUT_SECONDS = 60.0

_CHECKOUT_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class UiLaunchError(RuntimeError):
    """A launch that cannot go on, with a message written for the person who typed the command."""


def find_dist(explicit: str | None = None) -> Path | None:
    """The built UI: an explicit path, the environment variable, or this checkout's `web/dist`."""
    for candidate in (explicit, os.environ.get(DIST_ENV), str(_CHECKOUT_DIST)):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if (path / "index.html").is_file():
            return path.resolve()
    return None


def missing_dist_message() -> str:
    return (
        "找不到前端产物。先构建一次：\n"
        "  pnpm -C web install && pnpm -C web build\n"
        f"或用 --dist 指到产物目录（也可用环境变量 {DIST_ENV}）。"
    )


def api_is_up(port: int) -> bool:
    """Whether something already answers the API's health check on that port.

    The environment's proxy settings are ignored, and that is the whole point of the client below:
    on a machine with `http_proxy` set, a proxied loopback probe hangs until it times out, which
    reads exactly like "no backend there" — so the launch would start a second one and collide with
    the first. Model calls elsewhere in the kernel want that proxy; a loopback check never does.
    """
    try:
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            response = client.get(f"http://127.0.0.1:{port}/api/health")
    except (httpx.HTTPError, OSError):
        return False
    return response.status_code == 200


def port_is_free(port: int) -> bool:
    """Whether this process could bind the port, asked before spawning a child that could not."""
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    return True


def start_api(port: int, ui_origin: str) -> subprocess.Popen[bytes]:
    """Starts the API in its own process group, declaring this launcher's origin for the terminal.

    The origin is declared rather than inherited: the terminal's allowlist has no loopback default,
    so the page this launcher serves is named like any other page would have to be.
    """
    env = {**os.environ, ALLOWED_ORIGINS_ENV: ui_origin}
    return subprocess.Popen(
        [sys.executable, "-m", "avid", "web", "--port", str(port)],
        env=env,
        start_new_session=True,
    )


def wait_for_api(
    port: int, child: subprocess.Popen[bytes], timeout: float = HEALTH_TIMEOUT_SECONDS
) -> None:
    """Waits until the API answers, failing fast when the child has already given up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise UiLaunchError(
                f"后端进程退出了（code={child.returncode}）；它自己的报错就在上面。"
            )
        if api_is_up(port):
            return
        time.sleep(0.3)
    raise UiLaunchError(f"后端 {timeout:.0f} 秒内没有应答 /api/health")


def _stop_child(child: subprocess.Popen[bytes]) -> None:
    """Takes the child down, process group and all: an orphaned API keeps holding its port."""
    if child.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(child.pid), signal.SIGTERM)
        else:  # pragma: no cover - only Windows lands here, and it has no process groups
            child.terminate()
        child.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        child.kill()


def launch_ui(
    *,
    dist: str | None = None,
    api_port: int = DEFAULT_API_PORT,
    ui_port: int = DEFAULT_UI_PORT,
    open_window: bool = True,
    browser: str | None = None,
) -> int:
    """Runs until interrupted; returns the process exit code."""
    root = find_dist(dist)
    if root is None:
        print(missing_dist_message(), file=sys.stderr)
        return 2

    child: subprocess.Popen[bytes] | None = None
    stop_server: Callable[[], None] | None = None
    try:
        if api_is_up(api_port):
            print(f"后端已在 127.0.0.1:{api_port} 应答，直接用它", file=sys.stderr)
        elif not port_is_free(api_port):
            # Answering nothing yet the port is taken: starting a child here only moves the failure
            # one process further away, and its EADDRINUSE reads like a backend problem.
            raise UiLaunchError(
                f"端口 {api_port} 被占用，但那里没有 Avid 后端在应答"
                "（可能是别的程序，也可能是 Windows 侧占着的端口）；用 --port 换一个。"
            )
        else:
            child = start_api(api_port, f"http://127.0.0.1:{ui_port}")
            wait_for_api(api_port, child)
            print(f"后端已起：127.0.0.1:{api_port}", file=sys.stderr)

        try:
            _server, stop_server = serve(root, ui_port, f"http://127.0.0.1:{api_port}")
        except OSError as exc:
            raise UiLaunchError(f"界面端口 {ui_port} 起不来（{exc}）；用 --ui-port 换一个") from exc

        url = f"http://127.0.0.1:{ui_port}/"
        print(f"界面在 {url}（产物 {root}）", file=sys.stderr)
        if open_window:
            how = open_app_window(url, resolve_command(browser))
            if how == "none":
                print("这台机器上没有找到可用的浏览器，请手动打开上面的地址", file=sys.stderr)
            elif how == "browser-tab":
                print("已用默认浏览器打开（没找到 Chromium 系，拿不到无边框窗口）", file=sys.stderr)
        print("Ctrl-C 结束（会一并停掉它起的后端）", file=sys.stderr)

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n收尾中…", file=sys.stderr)
    except UiLaunchError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if stop_server is not None:
            stop_server()
        if child is not None:
            _stop_child(child)
    return 0


__all__ = [
    "ALLOWED_ORIGINS_ENV",
    "DEFAULT_API_PORT",
    "DEFAULT_UI_PORT",
    "DIST_ENV",
    "UiLaunchError",
    "api_is_up",
    "find_dist",
    "launch_ui",
    "missing_dist_message",
    "start_api",
    "wait_for_api",
]
