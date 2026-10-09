"""Entry chain to messages: reading a session back as one model call's input, synthesizing results for tool-call batches cut off before completion."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .types import MESSAGE_ENTRY, NOTICE_ENTRY, BranchScan, Entry
from .values import COMPACTION_NS, ValueAddress

__all__ = ["messages_for_branch", "entries_to_messages", "repair_incomplete_batches"]

# Both types project, because kernel-injected notices were part of the transcript the model actually saw.
_TRANSCRIPT_TYPES = (MESSAGE_ENTRY, NOTICE_ENTRY)

# Crash-window synthetic result: tells the model the outcome is unknown, and never reaches disk.
_CUT_OFF_RESULT = (
    "（此调用的结果没有落盘：运行在结果记录前被切断，执行状态未知，"
    "可能已生效。请先核实实际状态（读文件/查状态）再决定是否重做。）"
)


def messages_for_branch(session: Any, branch: str = "main") -> list[dict[str, Any]]:
    """Every message on a branch, oldest first, with a compaction cursor replacing the covered
    prefix by its summary plus the kept tail; an unknown branch yields an empty list.
    """
    found = session.branch(branch)
    if found is None:
        return []
    entries = found.find_entries(BranchScan(order="oldestFirst"))
    record = session.get_value(ValueAddress(COMPACTION_NS, branch))
    if record is not None and isinstance(record.value, dict):
        return _project_with_compaction(record.value, entries)
    return entries_to_messages(entries)


def _project_with_compaction(
    record: dict[str, Any], entries: Sequence[Entry]
) -> list[dict[str, Any]]:
    """The cursor anchors on an entry seq, so the covered prefix becomes the summary plus the
    kept tail.
    """
    try:
        through = int(record.get("through_seq") or 0)
        keep = int(record.get("keep") or 0)
    except (TypeError, ValueError):
        return entries_to_messages(entries)
    summary = record.get("summary")
    if through <= 0 or not isinstance(summary, dict):
        return entries_to_messages(entries)

    covered = [entry for entry in entries if entry.seq <= through]
    if not covered:
        return entries_to_messages(entries)
    rest = [entry for entry in entries if entry.seq > through]

    def transcript(items: list[Entry]) -> list[dict[str, Any]]:
        return [
            dict(entry.message)
            for entry in items
            if entry.type in _TRANSCRIPT_TYPES and entry.message is not None
        ]

    kept = transcript(covered)[-keep:] if keep > 0 else []
    return repair_incomplete_batches([dict(summary)] + kept + transcript(rest))


def entries_to_messages(entries: Sequence[Entry]) -> list[dict[str, Any]]:
    """Project transcript entries into message dicts and repair the incomplete tail."""
    messages = [
        dict(entry.message)
        for entry in entries
        if entry.type in _TRANSCRIPT_TYPES and entry.message is not None
    ]
    return repair_incomplete_batches(messages)


def repair_incomplete_batches(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the chain intact; calls whose results never arrived get a synthesized cut-off notice, orphan tool results are dropped."""
    kept: list[dict[str, Any]] = []
    pending: set[str] = set()
    call_order: list[str] = []

    def fill_missing() -> None:
        # Fill missing results in the order the assistant listed the calls.
        for call_id in call_order:
            if call_id in pending:
                kept.append(
                    {"role": "tool", "tool_call_id": call_id, "content": _CUT_OFF_RESULT}
                )
        pending.clear()
        call_order.clear()

    for message in messages:
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id"))
            # A result with no matching pending call is an orphan and is dropped.
            if call_id not in pending:
                continue
            pending.discard(call_id)
            kept.append(message)
            continue

        # A new non-tool message closes an unfinished batch: the assistant message and the
        # results that did arrive stay, the missing ones get the synthesized notice.
        if pending:
            fill_missing()

        calls = message.get("tool_calls") or []
        if calls:
            ids = [str(call.get("id")) for call in calls]
            pending = set(ids)
            call_order = list(dict.fromkeys(ids))
        kept.append(message)

    # A batch still pending at the end of the chain never completed either.
    if pending:
        fill_missing()
    return kept
