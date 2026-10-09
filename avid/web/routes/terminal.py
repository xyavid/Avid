"""WebSocket terminal bridge at ``/api/ws/terminal?root=<workspace root>&cols=&rows=``: one PTY
shell per connection, POSIX only, the connection owning the shell's whole lifecycle (disconnect
kills the process group, so no orphan shell survives).

``root`` must be a registered workspace root; the Origin hostname must be allowlisted and its
host:port must equal the Host header, because TrustBoundaryMiddleware never sees WebSocket
handshakes; frames are up ``{"type":"in","data"}`` / ``{"type":"resize","cols","rows"}``, down
``{"type":"out","data"}`` / ``{"type":"exit","code"}`` / ``{"type":"error","message"}``.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import logging
import os
import signal
import struct
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import current_services

logger = logging.getLogger("avid.web.terminal")

router = APIRouter()

_MAX_COLS_ROWS = 500


def _clamp(value: object, default: int) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(2, min(number, _MAX_COLS_ROWS))


@router.websocket("/ws/terminal")
async def terminal_socket(websocket: WebSocket, root: str = "", cols: int = 80, rows: int = 24) -> None:
    """One PTY per connection; the socket is the shell's whole lifecycle."""
    # BaseHTTPMiddleware never sees the WS handshake, so Origin is checked here on the allowlist.
    allowed_hosts = websocket.app.state.allowed_hosts
    origin = websocket.headers.get("origin")
    if origin:
        parts = urlsplit(origin)
        if (
            parts.hostname not in allowed_hosts
            or parts.netloc.lower() != websocket.headers.get("host", "").lower()
        ):
            logger.warning("拒绝终端连接的 Origin 不在信任边界内：%s", origin)
            await websocket.close(code=1008)
            return

    services = current_services(websocket)
    roots = {item["root"] for item in services.workspaces.list()}
    root_path = Path(root).expanduser().resolve() if root.strip() else None
    await websocket.accept()
    if root_path is None or str(root_path) not in roots:
        await websocket.send_json({"type": "error", "message": "root 必须是已登记工作区的根目录"})
        await websocket.close()
        return
    if os.name != "posix":
        await websocket.send_json(
            {"type": "error", "message": "终端面板暂不支持 Windows（标准库无 PTY；ConPTY 封装另立阶段）"}
        )
        await websocket.close()
        return

    # Runtime imports: top-level fcntl/pty would break the whole web service on Windows.
    import fcntl
    import pty
    import termios

    def set_size(fd: int, rows_n: int, cols_n: int) -> None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows_n, cols_n, 0, 0))

    rows_n, cols_n = _clamp(rows, 24), _clamp(cols, 80)
    master_fd, slave_fd = pty.openpty()
    set_size(master_fd, rows_n, cols_n)
    try:
        proc = subprocess.Popen(
            [os.environ.get("SHELL") or "bash"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(root_path),
            start_new_session=True,
            env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
        )
    except OSError as exc:
        os.close(master_fd)
        os.close(slave_fd)
        await websocket.send_json({"type": "error", "message": f"终端启动失败：{exc}"})
        await websocket.close()
        return
    os.close(slave_fd)  # The child holds a copy; the parent's own copy would break EOF semantics.
    logger.info("终端已连接：pid=%s cwd=%s (%sx%s)", proc.pid, root_path, cols_n, rows_n)

    loop = asyncio.get_running_loop()
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    outgoing: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:
        """Pumps PTY output into the queue; the blocking read runs on a thread."""
        while True:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = decoder.decode(data)
            loop.call_soon_threadsafe(outgoing.put_nowait, text)
        loop.call_soon_threadsafe(outgoing.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()

    async def sender() -> None:
        while True:
            text = await outgoing.get()
            if text is None:
                break
            await websocket.send_json({"type": "out", "data": text})
        # EOF from the PTY means the shell exited; poll() can normally read the code right away.
        with contextlib.suppress(Exception):
            code = proc.poll()
            await websocket.send_json({"type": "exit", "code": code if code is not None else 0})

    send_task = asyncio.create_task(sender())
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            try:
                frame = json.loads(message.get("text") or "{}")
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue  # A non-object frame (number/string/array) is malformed; skip, don't drop.
            kind = frame.get("type")
            # A dead PTY raises OSError; that is a normal shell exit, not a coroutine escape.
            with contextlib.suppress(OSError):
                if kind == "in" and isinstance(frame.get("data"), str):
                    os.write(master_fd, frame["data"].encode("utf-8"))
                elif kind == "resize":
                    set_size(master_fd, _clamp(frame.get("rows"), 24), _clamp(frame.get("cols"), 80))
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
        send_task.cancel()
        # sender may already have died on send_json after a disconnect; still finish fd cleanup.
        with contextlib.suppress(BaseException):
            await send_task
        with contextlib.suppress(OSError):
            os.close(master_fd)
        logger.info("终端已断开：pid=%s（exit=%s）", proc.pid, proc.returncode)
