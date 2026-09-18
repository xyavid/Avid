"""bash：在工作区根目录执行一条 shell 命令。

**这不是沙箱。** 命令以当前进程的权限运行，workspace.resolve() 那套路径校验
对它完全无效。这里只有三条最低护栏：工作目录、超时（连子孙一起清理）、输出上限。
真正的执行边界属于审批 / 权限模型。

为什么是 `bash -c` 而不是 `bash -lc`：登录 shell 每次都要 source /etc/profile 与
~/.bash_profile，**实测 0.41s/次**（20 条命令就是 8 秒纯启动开销），而且 profile 里
的 `cd`/PATH 改写会让"在工作区根目录执行"这条承诺不成立。代价是 profile 独有的
环境变量不再自动带上——命令的环境现在来自 `avid` 自己的进程环境（从终端启动就
继承了终端的环境）。出现"终端里能用、这里 command not found"时，再考虑加回 `-l`
或显式注入环境。

三条护栏的实现要点：

* **超时**：`threading.Timer` 到点 `killpg` 整个进程组——只 `kill()` 直接子进程时，
  `bash -c 'npm run …'` 起的一串子孙会活下来继续占端口/文件。
* **输出**：边读边限长。以前 `capture_output=True` 会把全部输出读进内存再截断到
  20000 字符，`yes` 这类命令直接把进程撑爆。
* **刷屏**：超过上限后还继续丢弃一小段余量；再多说明命令在刷屏，直接终止
  （否则读循环会一直跑下去，直到超时）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from . import workspace

if TYPE_CHECKING:  # 只用于标注：tools 不在运行时依赖 runtime 的实例类型
    from ..runtime.state import RunState

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300
MAX_OUTPUT_CHARS = 20000
# 超过上限后还允许继续读多少字符：给"输出略多但很快结束"的命令留余量；
# 再多就判定为刷屏并终止。内存上限因此是 12 × MAX_OUTPUT_CHARS（约 240 KB）。
DRAIN_FACTOR = 12
READ_CHUNK = 8192


def _timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1, min(seconds, MAX_TIMEOUT))


class _Bounded:
    """有上限的字符收集器：到上限后只计数、不再存。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: list[str] = []
        self.size = 0
        self.total = 0

    def feed(self, piece: str) -> None:
        self.total += len(piece)
        if self.size < self.limit:
            room = self.limit - self.size
            self.parts.append(piece[:room])
            self.size += min(room, len(piece))

    @property
    def text(self) -> str:
        return "".join(self.parts)

    @property
    def truncated(self) -> bool:
        return self.total > self.size

    @property
    def flooded(self) -> bool:
        """输出远超上限：继续读下去没有意义，调用方应当终止命令。"""
        return self.total > self.limit * DRAIN_FACTOR


def _read_into(
    stream: IO[str] | None, collector: _Bounded, on_flood: Callable[[], None]
) -> None:
    """读线程体：进程被杀时管道会抛错，那属于正常路径。

    判定为刷屏时**当场**触发终止：等两个读线程都 join 完再处理会卡在另一条
    没有数据的管道上（`yes` 只刷 stdout，stderr 的 read 会一直等），于是"读不下
    去了"要等到超时才生效——实测就是这样把 30 秒耗满的。
    """
    if stream is None:  # pragma: no cover - Popen 一定给了管道
        return
    try:
        while True:
            piece = stream.read(READ_CHUNK)
            if not piece:
                return
            collector.feed(piece)
            if collector.flooded:
                on_flood()
                return
    except (OSError, ValueError):  # pragma: no cover - 取决于 kill 的时机
        return


def _kill_group(process: subprocess.Popen) -> None:
    """连子孙一起终止。拿不到进程组（已退出）就退回 kill 自己。"""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        with suppress(OSError):  # pragma: no cover - 已经退出
            process.kill()


def bash(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "错误：缺少参数 command"

    raw_root = getattr(state, "workspace_root", None)
    cwd: Path = Path(raw_root) if raw_root else workspace.WORKSPACE_ROOT
    timeout = _timeout(args.get("timeout_seconds"))
    try:
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            # 自成进程组：超时才能连子孙一起清掉。
            start_new_session=True,
        )
    except OSError as exc:
        return f"错误：无法执行命令：{exc}"

    out, err = _Bounded(MAX_OUTPUT_CHARS), _Bounded(MAX_OUTPUT_CHARS)
    readers = [
        threading.Thread(
            target=_read_into,
            args=(process.stdout, out, lambda: _kill_group(process)),
            daemon=True,
        ),
        threading.Thread(
            target=_read_into,
            args=(process.stderr, err, lambda: _kill_group(process)),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    timed_out = threading.Event()

    def on_deadline() -> None:
        timed_out.set()
        _kill_group(process)

    watchdog = threading.Timer(timeout, on_deadline)
    watchdog.start()
    try:
        for reader in readers:
            reader.join()
        returncode = process.wait()
    finally:
        watchdog.cancel()

    if timed_out.is_set():
        return f"错误：命令超时，超过 {timeout} 秒未结束，已终止"

    parts = []
    if out.text:
        parts.append(out.text.rstrip("\n"))
    if err.text:
        parts.append("[stderr]\n" + err.text.rstrip("\n"))
    parts.append(f"[exit {returncode}]")

    output = "\n".join(parts)
    flooded = out.flooded or err.flooded
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS]
    if flooded or out.truncated or err.truncated:
        seen = out.total + err.total
        if flooded:
            output += f"\n…（输出过多已终止命令，已收到 {seen} 字符）"
        else:
            output += f"\n…（输出已截断，原文 {seen} 字符）"
    return output
