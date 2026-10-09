"""Implements the file tools: read, write, edit and glob.

Failures come back as text so the loop continues; the only hard refusal is a sensitive-path
read (``security.action.sensitive_reason``), and both write paths take a write-ahead
checkpoint after validation and refuse to write when it fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...security.action import sensitive_reason
from .registry import tool
from .workspace import relative, resolve

if TYPE_CHECKING:  # annotation only: tools must not depend on runtime at run time
    from ..state import RunState

MAX_READ_CHARS = 20000
MAX_READ_LINES = 2000
MAX_GLOB_RESULTS = 200


def _root(state: "RunState | None") -> Path | None:
    """Returns the run's workspace root, or None to let the workspace module supply a default."""
    raw = getattr(state, "workspace_root", None)
    return Path(raw) if raw else None


def _outside_ok(state: "RunState | None") -> bool:
    """Returns True — outside-workspace access needs no authorization; kept for call-site
    readability."""
    _ = state
    return True


def _protected(state: "RunState | None", path: Path) -> str | None:
    """The file tools' only hard refusal: a sensitive path; everything else passes through."""
    _ = state
    reason = sensitive_reason(str(path))
    return None if reason is None else f"错误：受保护的宿主资源（{reason}），任何确认都无效"


def _snapshot_before_write(state: "RunState | None", path: Path) -> str | None:
    """Takes the write-ahead checkpoint after validation; None lets the write proceed, while an
    error line means the backup failed and the caller must not write."""
    if state is None:
        return None
    checkpointer = state.checkpoint
    if checkpointer is None:
        return None
    return checkpointer.snapshot(path)


def _int(value: Any, *, default: int, minimum: int) -> int:
    """Parses an integer argument, falling back to the default and clamping to the minimum."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(number, minimum)


def _count_lines(path: Path) -> int:
    """Counts a file's lines by streaming it, since splitting would materialize every line."""
    total = 0
    tail_ends_with_newline = True
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                total += chunk.count(b"\n")
                tail_ends_with_newline = chunk.endswith(b"\n")
    except OSError:
        return total
    if total and not tail_ends_with_newline:
        total += 1  # a final line without a newline still counts
    return total


def _read_window(handle: Any, offset: int, limit: int) -> tuple[list[str], bool]:
    """Reads at most ``limit`` lines from ``offset``, holding only the window in memory."""
    window: list[str] = []
    hit_limit = False
    for number, line in enumerate(handle, start=1):
        if number < offset:
            continue
        if len(window) == limit:
            hit_limit = True  # the current line stayed out, so more lines follow
            break
        window.append(line[:-1] if line.endswith("\n") else line)
    return window, hit_limit


@tool(
    name="read_file",
    description="读取工作区内文本文件的内容，按行返回。默认从第 1 行起最多读 2000 行，"
    "超过 20000 字符的部分会被截断；两种情况都会在末尾提示，可用 offset / limit 继续读。",
    properties={
        "path": {
            "type": "string",
            "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
        },
        "offset": {
            "type": "integer",
            "description": "起始行号，从 1 开始，默认 1。",
            "minimum": 1,
        },
        "limit": {
            "type": "integer",
            "description": "本次最多读取的行数，默认 2000。",
            "minimum": 1,
        },
    },
    required=("path",),
    concurrency="safe",
)
def read_file(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Returns the requested window of a text file, with a notice when it was truncated."""
    raw = str(args.get("path", ""))
    path, error = resolve(raw, root=_root(state), outside_ok=_outside_ok(state))
    if error:
        return f"错误：{error}"
    assert path is not None  # resolve yields exactly one of a path or an error
    blocked = _protected(state, path)
    if blocked:
        return blocked
    if not path.exists():
        return f"错误：文件不存在：{raw}"
    if path.is_dir():
        return f"错误：{raw} 是目录；列目录用 glob，或用 bash 的 ls"

    offset = _int(args.get("offset"), default=1, minimum=1)
    limit = _int(args.get("limit"), default=MAX_READ_LINES, minimum=1)

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            window, hit_limit = _read_window(handle, offset, limit)
    except OSError as exc:
        return f"错误：读取失败：{exc}"

    if not window:
        if offset == 1:
            return ""  # empty file
        return f"错误：offset {offset} 超出文件范围（共 {_count_lines(path)} 行）"

    text = "\n".join(window)
    reasons = []
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS]
        reasons.append("字符数")
    if hit_limit:
        reasons.append(f"行数（共 {_count_lines(path)} 行）")
    if reasons:
        text += f"\n…（已按{'、'.join(reasons)}截断，可调 offset / limit 继续读）"
    return text


@tool(
    name="write_file",
    description="把内容整体写入工作区内的文件：文件已存在则覆盖，缺失的父目录会自动创建。"
    "只改几行请用 edit_file，不要为了局部修改而整体重写。",
    properties={
        "path": {
            "type": "string",
            "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
        },
        "content": {
            "type": "string",
            "description": "要写入的完整文件内容，UTF-8 编码。",
        },
    },
    required=("path", "content"),
    concurrency="exclusive",
    writes=True,
)
def write_file(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Writes a whole file, creating missing parents, and reports the new line count."""
    raw = str(args.get("path", ""))
    path, error = resolve(raw, root=_root(state), outside_ok=_outside_ok(state))
    if error:
        return f"错误：{error}"
    assert path is not None  # resolve yields exactly one of a path or an error
    blocked = _protected(state, path)
    if blocked:
        return blocked

    content = args.get("content")
    if not isinstance(content, str):
        return "错误：缺少参数 content，或它不是字符串"
    if path.is_dir():
        return f"错误：{raw} 是目录"

    refused = _snapshot_before_write(state, path)
    if refused:
        return refused

    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"错误：写入失败：{exc}"

    lines = content.count("\n") + 1
    return f"{'已覆盖' if existed else '已新建'} {raw}（{lines} 行，{len(content)} 字符）"


@tool(
    name="edit_file",
    description="把文件中的 old_string 精确替换为 new_string，只替换一次。"
    "old_string 必须在文件中恰好出现一次：出现 0 次或多次都不做修改并报错，"
    "多次时需要附带更多上下文让它唯一。",
    properties={
        "path": {
            "type": "string",
            "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
        },
        "old_string": {
            "type": "string",
            "description": "要被替换的原文，必须与文件内容逐字符一致且在文件中唯一。",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的文本；传空字符串表示删除这段内容。",
        },
    },
    required=("path", "old_string", "new_string"),
    concurrency="exclusive",
    writes=True,
)
def edit_file(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Replaces one exactly-once occurrence of old_string, refusing anything ambiguous."""
    raw = str(args.get("path", ""))
    path, error = resolve(raw, root=_root(state), outside_ok=_outside_ok(state))
    if error:
        return f"错误：{error}"
    assert path is not None  # resolve yields exactly one of a path or an error
    blocked = _protected(state, path)
    if blocked:
        return blocked

    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not old:
        return "错误：缺少参数 old_string，或它为空字符串"
    if not isinstance(new, str):
        return "错误：缺少参数 new_string（删除内容时传空字符串）"
    if not path.exists():
        return f"错误：文件不存在：{raw}"
    if path.is_dir():
        return f"错误：{raw} 是目录"

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"错误：读取失败：{exc}"

    occurrences = text.count(old)
    if occurrences == 0:
        return "错误：old_string 在文件中不存在，未做任何修改"
    if occurrences > 1:
        return (
            f"错误：old_string 在文件中出现 {occurrences} 次，不唯一；"
            "请带上更多上下文让它唯一"
        )

    refused = _snapshot_before_write(state, path)
    if refused:
        return refused

    try:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    except OSError as exc:
        return f"错误：写入失败：{exc}"
    return f"已替换 {raw} 中的 1 处文本"


@tool(
    name="glob",
    description="按 glob 模式在工作区内查找文件，返回相对路径列表，例如 **/*.py、src/**/*.md。"
    "只匹配文件名，不搜索文件内容；* 不匹配以点开头的文件。",
    properties={
        "pattern": {
            "type": "string",
            "description": "glob 模式，例如 **/*.py。",
        },
        "path": {
            "type": "string",
            "description": '起始目录，相对工作区根目录，默认 "."。',
        },
    },
    required=("pattern",),
    concurrency="safe",
)
def glob_files(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Lists files matching a glob pattern, capped at MAX_GLOB_RESULTS with a notice."""
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return "错误：缺少参数 pattern"

    raw = str(args.get("path", ".") or ".")
    root, error = resolve(raw, root=_root(state), outside_ok=_outside_ok(state))
    if error:
        return f"错误：{error}"
    assert root is not None  # resolve yields exactly one of a path or an error
    blocked = _protected(state, root)
    if blocked:
        return blocked
    if not root.is_dir():
        return f"错误：{raw} 不是目录"

    try:
        matches = sorted(p for p in root.glob(pattern) if p.is_file())
    except (ValueError, OSError) as exc:
        return f"错误：模式非法或查找失败：{exc}"

    if not matches:
        return f"未找到匹配 {pattern} 的文件"

    shown = matches[:MAX_GLOB_RESULTS]
    text = "\n".join(relative(p, root=_root(state)) for p in shown)
    if len(matches) > len(shown):
        text += f"\n…（共 {len(matches)} 个匹配，只显示前 {len(shown)} 个）"
    return text
