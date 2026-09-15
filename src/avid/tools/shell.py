"""bash：在工作区根目录执行一条 shell 命令。

**这不是沙箱。** 命令以当前进程的权限运行，workspace.resolve() 那套路径校验
对它完全无效。这里只有三条最低护栏：工作目录、超时、输出截断。
真正的执行边界属于审批 / 权限模型。
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import workspace

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300
MAX_OUTPUT_CHARS = 20000


def _timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1, min(seconds, MAX_TIMEOUT))


def bash(args: dict[str, Any]) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "错误：缺少参数 command"

    timeout = _timeout(args.get("timeout_seconds"))
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=workspace.WORKSPACE_ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"错误：命令超时，超过 {timeout} 秒未结束，已终止"
    except OSError as exc:
        return f"错误：无法执行命令：{exc}"

    parts = []
    if completed.stdout:
        parts.append(completed.stdout.rstrip("\n"))
    if completed.stderr:
        parts.append("[stderr]\n" + completed.stderr.rstrip("\n"))
    parts.append(f"[exit {completed.returncode}]")

    output = "\n".join(parts)
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"\n…（输出已截断，原文 {len(output)} 字符）"
    return output
