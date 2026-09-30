"""Entry chain to messages: reading a session back as one model call's input, dropping unfinished tool-call batches."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .types import MESSAGE_ENTRY, NOTICE_ENTRY, BranchScan, Entry

__all__ = ["messages_for_branch", "entries_to_messages", "repair_incomplete_batches"]

# Both types project, because kernel-injected notices were part of the transcript the model actually saw.
_TRANSCRIPT_TYPES = (MESSAGE_ENTRY, NOTICE_ENTRY)


def messages_for_branch(session: Any, branch: str = "main") -> list[dict[str, Any]]:
    """Every message on a branch, oldest first; an unknown branch yields an empty list."""
    found = session.branch(branch)
    if found is None:
        return []
    entries = found.find_entries(BranchScan(order="oldestFirst"))
    return entries_to_messages(entries)


def entries_to_messages(entries: Sequence[Entry]) -> list[dict[str, Any]]:
    """Project transcript entries into message dicts and repair the incomplete tail."""
    messages = [
        dict(entry.message)
        for entry in entries
        if entry.type in _TRANSCRIPT_TYPES and entry.message is not None
    ]
    return repair_incomplete_batches(messages)


def repair_incomplete_batches(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop calls whose results never all arrived, plus orphan tool results, keeping the rest in order."""
    kept: list[dict[str, Any]] = []
    pending: set[str] = set()
    batch_start: int | None = None

    for message in messages:
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id"))
            # A result with no matching pending call is an orphan and is dropped.
            if call_id not in pending:
                continue
            pending.discard(call_id)
            kept.append(message)
            continue

        # A new non-tool message means the previous batch never finished, so drop its partial results.
        if pending:
            del kept[batch_start:]
            pending = set()
            batch_start = None

        calls = message.get("tool_calls") or []
        if calls:
            pending = {str(call.get("id")) for call in calls}
            batch_start = len(kept)
        kept.append(message)

    # A batch still pending at the end of the chain never completed either.
    if pending:
        del kept[batch_start:]
    return kept
