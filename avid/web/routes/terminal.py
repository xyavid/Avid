"""WebSocket 终端桥：一条连接一个 PTY 交互 shell，POSIX only。

- ``/api/ws/terminal?root=<工作区根>&cols=&rows=``
- root 必须是已登记工作区的根目录（防任意 cwd）；Origin 校验自己做——
  TrustBoundaryMiddleware 是 BaseHTTPMiddleware，不覆盖 WebSocket。
- 帧协议：上行 ``{"type":"in","data"}`` / ``{"type":"resize","cols","rows"}``；
  下行 ``{"type":"out","data"}`` / ``{"type":"exit","code"}`` / ``{"type":"error","message"}``。
- 断开杀整个进程组：终端是人的工具，不走权限引擎（打字的是人），
  但生命周期必须跟着连接走，不允许留下孤儿 shell。
- Windows 标准库没有 PTY（ConPTY 需要 ctypes 封装）：明确回 error frame，
  不做半吊子的管道伪装（记录缺口，另立阶段）。fcntl/pty/termios 因此
  只能在运行时导入——顶层导入会让 Windows 起不来整个 web 服务。
- 信任边界（M4 决策记录）：Origin 的 **hostname** 必须在白名单内（挡跨站与
  DNS rebinding），且 Origin 的 host:port 必须与请求 Host 头一致（挡本机
  其它端口的页面驱动 shell，即 CSWSH）。hostname 白名单不比端口，与 HTTP
  中间件同一姿态；本地其它端口的页面被端口比对挡住。
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
    # Origin 校验自己做：BaseHTTPMiddleware 只拦 HTTP，不拦 WebSocket 握手。
    # 白名单取 app 级的同一份（含 CLI 的 LAN extra），端口还要与 Host 一致。
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

    # POSIX 专属模块在这里才导入：顶层导入会让 Windows 起不来整个 web 服务。
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
    os.close(slave_fd)  # 子进程已持有副本，父进程留着自己的会挡住 EOF 语义
    logger.info("终端已连接：pid=%s cwd=%s (%sx%s)", proc.pid, root_path, cols_n, rows_n)

    loop = asyncio.get_running_loop()
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    outgoing: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:
        """把 PTY 输出泵进队列（阻塞读放线程，异步侧只消费）。"""
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
        # PTY 读到 EOF：shell 已退出。此刻它刚死，poll 通常已能拿到码。
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
                continue  # 非对象帧（数字/字符串/数组）是坏帧，跳过不杀连接
            kind = frame.get("type")
            # 对已死 PTY 的写与 resize 会 OSError——shell 自己退了（exit），按正常
            # 断开收尾；不让它从协程逃逸成 ASGI 未处理异常。
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
        # sender 可能已因对端断开死在 send_json 上：兜住一切，保证 fd 收尾必达。
        with contextlib.suppress(BaseException):
            await send_task
        with contextlib.suppress(OSError):
            os.close(master_fd)
        logger.info("终端已断开：pid=%s（exit=%s）", proc.pid, proc.returncode)
