"""文件类工具的实现：读、写、改、按模式查找。

失败一律返回以"错误："开头的文本，而不是抛异常——工具回传文本，
循环才能继续，模型才有机会自纠。
"""

from __future__ import annotations

from typing import Any

from .workspace import relative, resolve

MAX_READ_CHARS = 20000
MAX_READ_LINES = 2000
MAX_GLOB_RESULTS = 200


def _int(value: Any, *, default: int, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(number, minimum)


def read_file(args: dict[str, Any]) -> str:
    raw = str(args.get("path", ""))
    path, error = resolve(raw)
    if error:
        return f"错误：{error}"
    if not path.exists():
        return f"错误：文件不存在：{raw}"
    if path.is_dir():
        return f"错误：{raw} 是目录；列目录用 glob，或用 bash 的 ls"

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"错误：读取失败：{exc}"

    offset = _int(args.get("offset"), default=1, minimum=1)
    limit = _int(args.get("limit"), default=MAX_READ_LINES, minimum=1)
    window = lines[offset - 1 : offset - 1 + limit]

    if not window and lines:
        return f"错误：offset {offset} 超出文件范围（共 {len(lines)} 行）"

    text = "\n".join(window)
    reasons = []
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS]
        reasons.append("字符数")
    if offset - 1 + len(window) < len(lines):
        reasons.append(f"行数（共 {len(lines)} 行）")
    if reasons:
        text += f"\n…（已按{'、'.join(reasons)}截断，可调 offset / limit 继续读）"
    return text


def write_file(args: dict[str, Any]) -> str:
    raw = str(args.get("path", ""))
    path, error = resolve(raw)
    if error:
        return f"错误：{error}"

    content = args.get("content")
    if not isinstance(content, str):
        return "错误：缺少参数 content，或它不是字符串"
    if path.is_dir():
        return f"错误：{raw} 是目录"

    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"错误：写入失败：{exc}"

    lines = content.count("\n") + 1
    return f"{'已覆盖' if existed else '已新建'} {raw}（{lines} 行，{len(content)} 字符）"


def edit_file(args: dict[str, Any]) -> str:
    raw = str(args.get("path", ""))
    path, error = resolve(raw)
    if error:
        return f"错误：{error}"

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

    try:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    except OSError as exc:
        return f"错误：写入失败：{exc}"
    return f"已替换 {raw} 中的 1 处文本"


def glob_files(args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return "错误：缺少参数 pattern"

    raw = str(args.get("path", ".") or ".")
    root, error = resolve(raw)
    if error:
        return f"错误：{error}"
    if not root.is_dir():
        return f"错误：{raw} 不是目录"

    try:
        matches = sorted(p for p in root.glob(pattern) if p.is_file())
    except (ValueError, OSError) as exc:
        return f"错误：模式非法或查找失败：{exc}"

    if not matches:
        return f"未找到匹配 {pattern} 的文件"

    shown = matches[:MAX_GLOB_RESULTS]
    text = "\n".join(relative(p) for p in shown)
    if len(matches) > len(shown):
        text += f"\n…（共 {len(matches)} 个匹配，只显示前 {len(shown)} 个）"
    return text
