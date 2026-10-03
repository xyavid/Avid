"""Entry chain to messages: reading a session back as one model call's input, dropping unfinished tool-call batches."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .types import MESSAGE_ENTRY, NOTICE_ENTRY, BranchScan, Entry
from .values import COMPACTION_NS, ValueAddress

__all__ = ["messages_for_branch", "entries_to_messages", "repair_incomplete_batches"]

# Both types project, because kernel-injected notices were part of the transcript the model actually saw.
_TRANSCRIPT_TYPES = (MESSAGE_ENTRY, NOTICE_ENTRY)


def messages_for_branch(session: Any, branch: str = "main") -> list[dict[str, Any]]:
    """Every message on a branch, oldest first; an unknown branch yields an empty list.

    存在压缩游标时（诊断 C2），被游标覆盖的前缀由摘要（＋保留尾）替代——
    上一次运行花的摘要调用通过投影延续到之后的每个运行，不再重花。
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
    """游标锚定 entry seq（只追加、不可变），覆盖段由摘要＋保留尾替代。"""
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
