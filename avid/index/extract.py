"""哪条记录的哪段文本进 search_text —— 全库唯一一处规则。

改这里等于改索引的**内容**（不是结构），所以规则写在模块头而不是散在 SQL 里：

- `user` / `assistant` 正文全文进（这是人真正会搜的东西）；
- **Image parts index as one marker line** (name / type / size): a session is findable by
  screenshot, but base64 never enters the index (it would only add volume and exposure);
- `assistant` 的 tool_calls 也进（参数里有路径与命令：搜得到「哪个会话动过 pyproject.toml」），
  每个调用的参数截断，避免一次把大段 JSON 灌进去；
- `tool` 结果截前 2000 字符：够定位「哪个会话跑过这个命令」，不值得为全文付索引体积与
  敏感面（工具输出里可能有凭据、大文件内容）；
- `notice` / `error` 进（它们是给人看的行，同一段规则不加例外）；
- value 行一般不进（标题走会话级字段）；但**压缩摘要**进——它只落在游标值里、不是条目
  （`transcript.replace_all` 插入的那条摘要消息从不被 emit，所以它不是条目），而它概括了被压掉的
  那段历史，正是「我什么时候讨论过这个」要找的东西。

单条封顶 8000 字符。超限不是错误（不报 last_error）——只是搜不到尾部，这在「找会话」
这件事上可以接受，而它保证了一条离谱的巨无霸消息不会把库撑爆。

**改这里的规则要跑一次 `avid index rebuild`**：索引从 JSONL 增量补齐，它不会知道「内容规则变了」
（文件没动，游标没动）——只有重建才让旧文本按新规则重算。
"""

from __future__ import annotations

from typing import Any

from .. import attachments

# One tool result, truncated; enough to find the session that ran a command.
TOOL_TEXT_LIMIT = 2000
# One tool call's arguments, truncated.
TOOL_CALL_LIMIT = 500
# Whole row cap; anything longer is stored as a prefix.
TEXT_LIMIT = 8000
# Session titles and first-user lines are display facts; both are collapsed to one line.
DISPLAY_CHARS = 120


def _text_of(content: Any) -> str:
    """Message content as searchable text; image parts become a marker line, never base64."""
    return attachments.render_content_text(content)


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


def compaction_text(value: Any) -> str:
    """The compaction cursor's summary as searchable text.

    值是 `{"through_seq": …, "summary": <结构化检查点>, "keep": …}`：只取 summary 的**内容**
    （键名不进——搜 "facts" 命中一切毫无意义），按出现顺序拼成一段。
    """
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value.get("summary"))
    return "\n".join(part for part in parts if part).strip()[:TEXT_LIMIT]


def one_line(value: Any, *, limit: int = DISPLAY_CHARS) -> str | None:
    """Collapse a value to a single capped line for display; empty or non-text reads as None."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed[:limit] if collapsed else None


__all__ = [
    "DISPLAY_CHARS",
    "TEXT_LIMIT",
    "TOOL_CALL_LIMIT",
    "TOOL_TEXT_LIMIT",
    "compaction_text",
    "entry_text",
    "one_line",
]
