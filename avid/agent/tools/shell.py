"""Runs one shell command at the workspace root, bounded by the run's sandbox.

The permission layer builds the sandboxed command, and an unavailable sandbox hands the
boundary back to approvals.

"""


from __future__ import annotations

import base64
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from ...security.command_parse import is_read_only
from . import workspace
from .registry import tool

if TYPE_CHECKING:  # annotation only: tools must not depend on runtime at run time
    from ..runtime.state import RunState

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300
MAX_OUTPUT_CHARS = 20000
# Extra characters still read past the cap, which tolerates output that ends soon after and
# otherwise marks the command as flooding; it bounds memory at 12 x MAX_OUTPUT_CHARS.
DRAIN_FACTOR = 12
READ_CHUNK = 8192


class ShellUnavailableError(RuntimeError):
    """No usable shell interpreter on this platform; the tool reports it instead of guessing."""


# Windows PowerShell 5.1 emits the system codepage (GBK on zh-CN hosts), which the child
# pipes cannot decode reliably; forcing UTF-8 makes the output match the decoder below.
_UTF8_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def _encode_ps_command(script: str) -> str:
    """Base64 of UTF-16LE bytes — the quoting-proof way to hand PowerShell a script.

    ``-Command`` goes through Windows command-line escaping where embedded quotes and
    trailing backslashes can rewrite semantics; ``-EncodedCommand`` delivers the text
    verbatim. The downside is an opaque child cmdline in process listings, which the
    audit log covers by recording the model's command itself.
    """
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def shell_argv(command: str, *, platform: str | None = None) -> list[str]:
    """Builds the interpreter argv for one command: ``bash -c`` on POSIX, PowerShell on Windows.

    pwsh (PowerShell 7) is preferred, Windows PowerShell 5.1 is the fallback. The tool keeps
    the name ``bash`` for contract stability — the platform fact lives in the schema text.
    """
    system = platform if platform is not None else sys.platform
    if system != "win32":
        # sh keeps minimal POSIX systems (Alpine) working; a bashism fails at runtime
        # and the error reaches the model, which is better than refusing to start.
        resolved = shutil.which("bash") or shutil.which("sh")
        if resolved is None:
            raise ShellUnavailableError("找不到 bash 或 sh")
        return [resolved, "-c", command]
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise ShellUnavailableError("找不到 PowerShell（需要 pwsh 或 powershell 在 PATH 中）")
    # -NoProfile keeps startup scripts out of the child; -NonInteractive stops prompts hanging.
    return [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        _encode_ps_command(_UTF8_PREFIX + command),
    ]


def _assess_concurrency(arguments: dict[str, Any], state: Any) -> str:
    """A provably read-only command may share a segment; anything else is a barrier.

    bash 是这一档的第一个用户：`ls`/`git status`/`rg` 这类命令与别的读并行没有副作用，
    但**写命令必须独占**（它可能和同批的读抢同一个文件）。判定交给安全层的
    ``is_read_only``（建立在既有的 shell 解析事实上），拿不准就是独占。
    """
    _ = state
    command = arguments.get("command")
    return "safe" if isinstance(command, str) and is_read_only(command) else "exclusive"


def _timeout(value: Any) -> int:
    """Clamps a requested timeout into the supported range, falling back to the default."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1, min(seconds, MAX_TIMEOUT))


class _Bounded:
    """A character collector with a cap that keeps the tail and drops the earliest text.

    The newest lines carry the conclusion, so the uninformative opening is what gets dropped.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: deque[str] = deque()
        self.size = 0
        self.total = 0

    def feed(self, piece: str) -> None:
        """Appends a chunk and trims the oldest text until the collected size fits the cap."""
        self.total += len(piece)
        if self.limit <= 0:  # pragma: no cover - the cap is a constant, so never zero
            return
        self.parts.append(piece)
        self.size += len(piece)
        # Drop whole chunks first, while what remains still covers the whole cap.
        while len(self.parts) > 1 and self.size - len(self.parts[0]) >= self.limit:
            self.size -= len(self.parts.popleft())
        # Then trim the head chunk, which now only overshoots by part of its length.
        if self.size > self.limit:
            self.parts[0] = self.parts[0][self.size - self.limit :]
            self.size = self.limit

    @property
    def text(self) -> str:
        """The collected text, at most ``limit`` characters once anything was trimmed."""
        return "".join(self.parts)

    @property
    def truncated(self) -> bool:
        """Reports whether any text was dropped."""
        return self.total > self.size

    @property
    def flooded(self) -> bool:
        """Reports output far past the cap, where the caller should terminate the command."""
        return self.total > self.limit * DRAIN_FACTOR


def _read_into(
    stream: IO[str] | None, collector: _Bounded, on_flood: Callable[[], None]
) -> None:
    """Drains one pipe into a collector, stopping the command as soon as it floods.

    Flooding must terminate at once, since the other pipe may have no data to end its read.
    """
    if stream is None:  # pragma: no cover - Popen always provides the pipes
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
    except (OSError, ValueError):  # pragma: no cover - depends on when the kill lands
        return


def kill_tree(process: subprocess.Popen, *, platform: str | None = None) -> None:
    """Kills the whole process tree: the POSIX process group, or ``taskkill /T`` on Windows.

    taskkill walks the PID tree, which stands in for the process group there.
    """
    system = platform if platform is not None else sys.platform
    if system == "win32":
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603 - fixed argv built from the child's pid
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        with suppress(OSError):  # pragma: no cover - already exited
            process.kill()


@tool(
    name="bash",
    description="在工作区根目录执行一条 shell 命令，返回合并后的 stdout/stderr 与退出码。"
    "适合运行测试、构建、git、批量文本处理。每次调用都是独立的新 shell——"
    "需要切换目录时在同一条命令里用 cd。读写单个文件请优先用专用工具。"
    "POSIX 上经 bash（缺失时 sh）运行，Windows 上经 PowerShell 运行。",
    properties={
        "command": {
            "type": "string",
            "description": "要执行的 shell 命令（POSIX 经 bash/sh，Windows 经 PowerShell）。",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "超时秒数，默认 30，上限 300；超时后进程被终止。",
            "minimum": 1,
            # Matches shell.MAX_TIMEOUT, which a contract test asserts; the clamp in the
            # implementation is the second line of defence, this schema the first.
            "maximum": 300,
        },
    },
    required=("command",),
    # Spawns a process, so its working directory, timeout and output compete with the batch.
    concurrency="conditional",
    assess=_assess_concurrency,
)
def bash(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Runs one command, returning combined stdout and stderr plus an exit marker."""
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "错误：缺少参数 command"

    raw_root = getattr(state, "workspace_root", None)
    cwd: Path = Path(raw_root) if raw_root else workspace.WORKSPACE_ROOT
    timeout = _timeout(args.get("timeout_seconds"))

    # The sandbox comes precomputed from the permission layer, and no run spec (a direct call
    # or a unit test) means the caller is the host itself, so nothing is wrapped.
    security = getattr(state, "security", None)
    spec = getattr(security, "sandbox", None)
    try:
        argv = shell_argv(command)
    except ShellUnavailableError as exc:
        return f"错误：{exc}"
    env = None
    if spec is not None:
        grants = state.sandbox_grants() if state is not None else ()
        argv = spec.argv_prefix(argv, grants=grants, root=str(cwd))
        env = spec.child_env()
    # Decode the child to text: POSIX uses the locale, Windows is pinned to UTF-8 because
    # the argv prefix above forces PowerShell to emit UTF-8.
    decode: dict[str, Any] = (
        {"encoding": "utf-8", "errors": "replace"}
        if sys.platform == "win32"
        else {"text": True, "errors": "replace"}
    )
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(sys.platform != "win32"),
            **decode,
        )
    except OSError as exc:
        return f"错误：无法执行命令：{exc}"

    out, err = _Bounded(MAX_OUTPUT_CHARS), _Bounded(MAX_OUTPUT_CHARS)
    readers = [
        threading.Thread(
            target=_read_into,
            args=(process.stdout, out, lambda: kill_tree(process)),
            daemon=True,
        ),
        threading.Thread(
            target=_read_into,
            args=(process.stderr, err, lambda: kill_tree(process)),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    timed_out = threading.Event()

    def on_deadline() -> None:
        timed_out.set()
        kill_tree(process)

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

    # The exit marker and the truncation notice are the conclusion of the call, so they stay
    # out of the body truncation; losing the exit code would hide whether the command failed.
    dropped = len(body) > MAX_OUTPUT_CHARS - len(marker)
    if flooded:
        notice = f"…（输出过多已终止命令，已收到 {seen} 字符）"
    elif dropped or out.truncated or err.truncated:
        notice = f"…（输出已截断，原文 {seen} 字符）"
    else:
        notice = ""

    # The body keeps its tail, trimmed once more to the budget left after the conclusions.
    separators = 2 if notice else 1
    budget = max(0, MAX_OUTPUT_CHARS - len(marker) - len(notice) - separators)
    if len(body) > budget:
        body = body[-budget:] if budget else ""
    return "\n".join(part for part in (body, marker, notice) if part)
