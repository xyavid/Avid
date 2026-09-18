"""工作区边界：文件类工具共用的路径解析，以及"是否越界"的判定。

**这不是沙箱。** 它只保证文件类工具不越过工作区；bash 以本进程权限运行，不受这里约束。
``outside_command_target`` 对 bash 只是**启发式**扫描——变量展开、脚本文件、解释器内
构造的路径都能绕过它。它的用途是把"可能越界"变成一次确认，不是声称拦截。

根目录的解析基准按调用时读取 ``WORKSPACE_ROOT``（不是 import 时拷贝进签名默认值），
测试才能用 monkeypatch 把它换成临时目录；运行级的工作区根由调用方显式传入 ``root=``。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

# 在 import 时固定；测试通过 monkeypatch 替换成临时目录。
WORKSPACE_ROOT = Path.cwd()

# bash 里可能带路径的记号：按空白与 shell 元字符切开。
_TOKEN_SPLIT = re.compile(r"[\s;|&()<>'\"]+")
_PATHLIKE = re.compile(r"(?:^|/)(?:\.\.?)(?:/|$)")


def _base(root: Path | None = None) -> Path:
    return (root or WORKSPACE_ROOT).resolve()


def is_within(path: Path, base: Path) -> bool:
    """路径是否在 base 之内（含 base 本身）。"""
    return path == base or base in path.parents


def resolve(
    raw: str,
    *,
    root: Path | None = None,
    outside_ok: Callable[[Path], bool] | bool | None = None,
) -> tuple[Path | None, str | None]:
    """把路径解析成工作区内的绝对路径。

    返回 (path, error)：解析失败时 path 为 None，error 是可直接回传给模型的文本。

    ``outside_ok`` 是**权限层已经做出的决定**（账本里记着这个目标、或 ``system`` 模式）：
    真值则放行区外路径。缺省为假——工具自己不判断、只读结果，因此"没有授权"必然失败关闭。
    """
    text = raw.strip()
    if not text:
        return None, "路径为空"

    base = _base(root)
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()

    if not is_within(path, base):
        allowed = outside_ok(path) if callable(outside_ok) else bool(outside_ok)
        if not allowed:
            return None, f"拒绝访问工作区外的路径：{raw}"
    return path, None


def relative(path: Path, *, root: Path | None = None) -> str:
    """回传给模型时一律用工作区相对路径，输出更短也更稳定。"""
    base = _base(root)
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def outside_target(raw: str, *, root: Path | None = None) -> str | None:
    """文件类工具的目标是否在工作区之外：是则返回绝对路径字符串。

    与 :func:`resolve` 共用同一份路径数学——判定与执行不可能给出不同答案。
    """
    text = raw.strip()
    if not text:
        return None
    base = _base(root)
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    return None if is_within(path, base) else str(path)


def _candidate(token: str, base: Path) -> Path | None:
    """把一个命令记号解读成路径；不象路径的返回 None。"""
    token = token.strip().rstrip(",;)")
    if not token:
        return None
    if token.startswith("~"):
        return Path.home() / token[2:] if token.startswith("~/") else Path.home()
    if token.startswith("$HOME"):
        return Path.home() / token[6:] if token.startswith("$HOME/") else Path.home()
    if token.startswith("/"):
        return Path(token)
    if token == ".." or token.startswith("../") or _PATHLIKE.search(token):
        return base / token
    return None


def outside_command_target(command: str, *, root: Path | None = None) -> str | None:
    """bash 命令里是否出现工作区之外的路径：是则返回该绝对路径。

    启发式：只看字面量记号（绝对路径、``~``、``$HOME``、含 ``..`` 的相对路径）。
    变量拼接、写入脚本再执行、解释器内构造的路径都绕得过去——这是护栏，不是拦截。
    """
    if not isinstance(command, str) or not command.strip():
        return None
    base = _base(root)

    for token in _TOKEN_SPLIT.split(command):
        candidate = _candidate(token, base)
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if not is_within(resolved, base):
            return str(resolved)
    return None
