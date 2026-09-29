"""bash：在工作区根目录执行一条 shell 命令。

**命令本身是沙箱里的**：argv 由 ``state.security.sandbox`` 组装（只读系统 + 可写工作区
+ 掩蔽的宿主凭据 + 无出网的 network namespace + 裁过的环境），能力授予按已批准的路径
以额外 ``--ro-bind``/``--bind`` 挂进来。workspace.resolve() 那套路径校验对 bash 无效
（它是启发式），真正拦住命令的是挂载与 netns。

沙箱不可用或本次运行显式关掉沙箱（full）时套不上，这时兜底的是审批/分类器——
``SandboxSpec.degraded`` 会让决策层把边界挪回人身上，工具层不做决定、也不假装。

本文件另有三条护栏：工作目录、超时（连子孙一起清理）、输出上限。

为什么是 `bash -c` 而不是 `bash -lc`：登录 shell 每次都要 source /etc/profile 与
~/.bash_profile，**实测 0.41s/次**（20 条命令就是 8 秒纯启动开销），而且 profile 里
的 `cd`/PATH 改写会让"在工作区根目录执行"这条承诺不成立。代价是 profile 独有的
环境变量不再自动带上——命令的环境现在来自 `avid` 自己的进程环境（从终端启动就
继承了终端的环境）。出现"终端里能用、这里 command not found"时，再考虑加回 `-l`
或显式注入环境。

三条护栏的实现要点：

* **超时**：`threading.Timer` 到点 `killpg` 整个进程组——只 `kill()` 直接子进程时，
  `bash -c 'npm run …'` 起的一串子孙会活下来继续占端口/文件。
* **输出**：边读边限长，超限后保留**尾部**（最近的行才是要看的：报错、汇总、进度）；
  退出码与截断提示永远留在结果里、不参与正文截断，否则超长输出会把 `[exit N]` 一起
  切掉，模型分不清"命令失败了"和"输出被截断了"。
* **刷屏**：超过上限后还继续丢弃一小段余量；再多说明命令在刷屏，直接终止
  （否则读循环会一直跑下去，直到超时）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from . import workspace
from .registry import tool

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
    """有上限的字符收集器：超过上限后丢掉**最早**的部分，保留最近的。

    保留尾部而不是头部：命令输出里最新的行才是结论（报错、汇总、进度），长输出的
    开头往往最没有信息量。内存上限与改法之前一样是一个常量（不随输出增长）。
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: deque[str] = deque()
        self.size = 0
        self.total = 0

    def feed(self, piece: str) -> None:
        self.total += len(piece)
        if self.limit <= 0:  # pragma: no cover - 上限来自常量，正常不会是 0
            return
        self.parts.append(piece)
        self.size += len(piece)
        # 先整块丢：剩下的部分仍覆盖整个上限时，队首那块可以整体扔掉。
        while len(self.parts) > 1 and self.size - len(self.parts[0]) >= self.limit:
            self.size -= len(self.parts.popleft())
        # 再切块：队首只多出一部分时，从它开头切掉多出来的字。
        if self.size > self.limit:
            self.parts[0] = self.parts[0][self.size - self.limit :]
            self.size = self.limit

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


@tool(
    name="bash",
    description="在工作区根目录执行一条 shell 命令，返回合并后的 stdout/stderr 与退出码。"
    "适合运行测试、构建、git、批量文本处理。每次调用都是独立的新 shell——"
    "需要切换目录时在同一条命令里用 cd。读写单个文件请优先用专用工具。",
    properties={
        "command": {
            "type": "string",
            "description": "要执行的 shell 命令，通过 bash -lc 运行。",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "超时秒数，默认 30，上限 300；超时后进程被终止。",
            "minimum": 1,
            # 与 shell.MAX_TIMEOUT 一致（test_tools_contract 有一条断言钉住两者相等）：
            # 实现里的 clamp 现在是第二道防线，schema 才是给模型的第一道。
            "maximum": 300,
        },
    },
    required=("command",),
    # 起子进程：工作目录、超时、输出、刷屏都与同批的其它调用互相影响。
    concurrency="exclusive",
)
def bash(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "错误：缺少参数 command"

    raw_root = getattr(state, "workspace_root", None)
    cwd: Path = Path(raw_root) if raw_root else workspace.WORKSPACE_ROOT
    timeout = _timeout(args.get("timeout_seconds"))

    # 沙箱是**策略层算好的事实**：工具只负责套上去（"没有授权就回绝"同理）。
    # 没有运行级规格（直调工具、单元测试）时不套——那时调用方就是宿主自己。
    security = getattr(state, "security", None)
    spec = getattr(security, "sandbox", None)
    argv = ["bash", "-c", command]
    env = None
    if spec is not None:
        grants = state.sandbox_grants() if state is not None else ()
        argv = spec.argv_prefix(argv, grants=grants, root=str(cwd))
        env = spec.child_env()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
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

    marker = f"[exit {returncode}]"
    seen = out.total + err.total
    flooded = out.flooded or err.flooded

    body_parts = []
    if out.text:
        body_parts.append(out.text.rstrip("\n"))
    if err.text:
        body_parts.append("[stderr]\n" + err.text.rstrip("\n"))
    body = "\n".join(body_parts)

    # 退出码与截断提示不参与正文截断：它们是这次调用的结论。超长输出若把 `[exit N]`
    # 一起切掉，模型就分不清"命令失败了"和"输出被截断了"。
    dropped = len(body) > MAX_OUTPUT_CHARS - len(marker)
    if flooded:
        notice = f"…（输出过多已终止命令，已收到 {seen} 字符）"
    elif dropped or out.truncated or err.truncated:
        notice = f"…（输出已截断，原文 {seen} 字符）"
    else:
        notice = ""

    # 正文保尾：`_Bounded` 已按尾部收集，这里再按"扣掉结论部分后的额度"切一次。
    separators = 2 if notice else 1
    budget = max(0, MAX_OUTPUT_CHARS - len(marker) - len(notice) - separators)
    if len(body) > budget:
        body = body[-budget:] if budget else ""
    return "\n".join(part for part in (body, marker, notice) if part)
