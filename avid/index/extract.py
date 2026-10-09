"""The single place deciding which text of an entry becomes `search_text`: message bodies, tool-call
arguments and compaction summaries go in, images become a marker line and never base64.

Tool results are truncated, the whole row is capped at 8000 characters, and changing this
rule requires one `avid index rebuild` because incremental scans cannot notice it.
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
    """The compaction cursor's summary as searchable text: only the summary's own content,
    in order, with the keys left out.
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
