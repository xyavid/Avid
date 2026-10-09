"""哪条记录的哪段文本进 search_text —— 全库唯一一处规则。

改这里等于改索引的**内容**（不是结构），所以规则写在模块头而不是散在 SQL 里：

- `user` / `assistant` 正文全文进（这是人真正会搜的东西）；
- `assistant` 的 tool_calls 也进（参数里有路径与命令：搜得到「哪个会话动过 pyproject.toml」），
  每个调用的参数截断，避免一次把大段 JSON 灌进去；
- `tool` 结果截前 2000 字符：够定位「哪个会话跑过这个命令」，不值得为全文付索引体积与
  敏感面（工具输出里可能有凭据、大文件内容）；
- `notice` / `error` 进（它们是给人看的行，同一段规则不加例外）；
- value 行一律不进：标题走会话级字段，压缩摘要本身就是一条 entry（阶段 43 起如此），
  不需要从游标值里再抠一遍。

单条封顶 8000 字符。超限不是错误（不报 last_error）——只是搜不到尾部，这在「找会话」
这件事上可以接受，而它保证了一条离谱的巨无霸消息不会把库撑爆。
"""

from __future__ import annotations

from typing import Any

# One tool result, truncated; enough to find the session that ran a command.
TOOL_TEXT_LIMIT = 2000
# One tool call's arguments, truncated.
TOOL_CALL_LIMIT = 500
# Whole row cap; anything longer is stored as a prefix.
TEXT_LIMIT = 8000
# Session titles and first-user lines are display facts; both are collapsed to one line.
DISPLAY_CHARS = 120


def _text_of(content: Any) -> str:
    """Message content as searchable text: strings as-is, structured parts flattened, others ignored."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 分块内容（将来若出现图片等）只取其中的文本段
        parts = [item.get("text") for item in content if isinstance(item, dict)]
        return "\n".join(str(part) for part in parts if isinstance(part, str))
    return ""


def _tool_calls_text(message: dict[str, Any]) -> str:
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return ""
    chunks: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        arguments = function.get("arguments")
        text = str(arguments)[:TOOL_CALL_LIMIT] if arguments is not None else ""
        chunk = f"{name} {text}".strip() if name else text
        if chunk:
            chunks.append(chunk)
    return "\n".join(chunks)


def entry_text(entry_type: str, message: dict[str, Any] | None) -> tuple[str | None, str]:
    """One entry → (role, search_text). Role is None for entries that carry no message."""
    if not isinstance(message, dict):
        return None, ""

    raw_role = message.get("role")
    role = raw_role if isinstance(raw_role, str) else None
    limit = TOOL_TEXT_LIMIT if role == "tool" else TEXT_LIMIT

    parts = [_text_of(message.get("content"))]
    if role == "assistant":
        parts.append(_tool_calls_text(message))

    combined = "\n".join(part for part in parts if part).strip()
    return role, combined[:limit]


def one_line(value: Any, *, limit: int = DISPLAY_CHARS) -> str | None:
    """Collapse a value to a single capped line for display; empty or non-text reads as None."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed[:limit] if collapsed else None


__all__ = ["DISPLAY_CHARS", "TEXT_LIMIT", "TOOL_CALL_LIMIT", "TOOL_TEXT_LIMIT", "entry_text", "one_line"]
