"""Sole owner of the messages list, guarding that every assistant tool_call has a paired result."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


class TranscriptError(ValueError):
    """A change was rejected because it would break the message structure."""


def text_of(value: Any) -> str:
    """Render a message field as text for estimation and persistence."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def validate(messages: list[dict[str, Any]]) -> list[str]:
    """Return the list of structural violations; an empty list means the messages are valid."""
    problems: list[str] = []
    expected: set[str] = set()

    for index, message in enumerate(messages):
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id"))
            if call_id not in expected:
                problems.append(f"#{index} 的 tool 结果没有对应的 tool_call：{call_id}")
            else:
                expected.discard(call_id)
            continue

        if expected:
            problems.append(f"#{index} 之前有 {len(expected)} 个 tool_call 没收到结果")
            expected = set()

        for call in message.get("tool_calls") or []:
            expected.add(str(call.get("id")))

    if expected:
        problems.append(f"结尾有 {len(expected)} 个 tool_call 没收到结果")
    return problems


# Rough structural overhead per message (role, braces, separators).
_MESSAGE_OVERHEAD = 16


def message_chars(message: dict[str, Any]) -> int:
    """Estimate one message's character cost: content plus serialized tool_calls plus overhead."""
    total = len(text_of(message.get("content"))) + _MESSAGE_OVERHEAD
    calls = message.get("tool_calls")
    if calls:
        total += len(json.dumps(calls, ensure_ascii=False, default=str))
    return total


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    """Estimate the character count of a whole message list with a full scan."""
    return sum(message_chars(message) for message in messages)


class Transcript:
    """A message sequence that is structurally valid after every write."""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages: list[dict[str, Any]] = messages if messages is not None else []
        problems = validate(self._messages)
        if problems:
            raise TranscriptError("初始 messages 结构非法：" + "；".join(problems))
        # Character costs are maintained incrementally: the context is estimated several times per
        # turn, while only appends and content edits change it.
        self._chars = 0
        self._tool_chars = 0
        self._recompute_costs()

    def __len__(self) -> int:
        return len(self._messages)

    def as_messages(self) -> list[dict[str, Any]]:
        """Return a copy of the messages for a model call; nested tool_calls lists are shared."""
        return [dict(message) for message in self._messages]

    def text_at(self, index: int) -> str:
        return text_of(self._messages[index].get("content"))

    def tool_indexes(self) -> list[int]:
        return [i for i, m in enumerate(self._messages) if m.get("role") == "tool"]

    def last_user_index(self) -> int | None:
        for index in range(len(self._messages) - 1, -1, -1):
            if self._messages[index].get("role") == "user":
                return index
        return None

    def tool_chars(self) -> int:
        """Return the total characters of tool results, maintained in O(1)."""
        return self._tool_chars

    def estimate_chars(self) -> int:
        """Return the estimated characters of the whole context, maintained in O(1)."""
        return self._chars

    def _recompute_costs(self) -> None:
        """Recompute both cached costs after a bulk replacement."""
        self._chars = estimate_chars(self._messages)
        self._tool_chars = sum(
            len(self.text_at(index)) for index in self.tool_indexes()
        )

    def _track(self, message: dict[str, Any], sign: int = 1) -> None:
        """Add or remove one message's cost from the caches."""
        self._chars += sign * message_chars(message)
        if message.get("role") == "tool":
            self._tool_chars += sign * len(text_of(message.get("content")))

    def validate(self) -> list[str]:
        return validate(self._messages)

    def is_safe_boundary(self, index: int) -> bool:
        """Report whether index is a safe cut point: no pending tool_calls and no tool message."""
        if index <= 0 or index >= len(self._messages):
            return True
        if self._messages[index].get("role") == "tool":
            return False
        return not self._messages[index - 1].get("tool_calls")

    def append(self, message: dict[str, Any]) -> None:
        self._messages.append(message)
        self._track(message)

    def append_many(self, messages: Iterable[dict[str, Any]]) -> None:
        incoming = list(messages)
        self._messages.extend(incoming)
        for message in incoming:
            self._track(message)

    def set_content(self, index: int, content: str) -> None:
        """Replace one message's content; the structure is untouched, so nothing is validated."""
        if not 0 <= index < len(self._messages):
            raise TranscriptError(f"消息下标越界：{index}")
        message = self._messages[index]
        self._track(message, sign=-1)
        message["content"] = content
        self._track(message)

    def replace_all(self, messages: list[dict[str, Any]]) -> None:
        """Replace every message; a structure-breaking candidate raises and changes nothing."""
        candidate = list(messages)
        self._accept(candidate, "整体替换")
        self._messages[:] = candidate
        self._recompute_costs()

    def splice(
        self,
        start: int,
        stop: int,
        replacement: Iterable[dict[str, Any]] = (),
    ) -> None:
        """Replace [start, stop) with replacement, raising without changes if it is invalid."""
        if not (0 <= start <= stop <= len(self._messages)):
            raise TranscriptError(f"splice 区间非法：{start}..{stop}")
        candidate = self._messages[:start] + list(replacement) + self._messages[stop:]
        self._accept(candidate, f"裁剪 {start}..{stop}")
        self._messages[:] = candidate
        self._recompute_costs()

    @staticmethod
    def _accept(candidate: list[dict[str, Any]], what: str) -> None:
        problems = validate(candidate)
        if problems:
            raise TranscriptError(f"{what} 会破坏消息结构：" + "；".join(problems))
