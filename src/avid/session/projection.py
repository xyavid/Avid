"""条目链 → messages：续接时把会话读回成一次模型调用的输入。

只有一件事需要额外处理：**没有结果的 tool_calls 批次**。会话是逐条提交的，
进程可能在 ``assistant(tool_calls)`` 与它的 tool 结果之间被杀掉，于是链里
留下一批没有结果的调用。pi 用 checkpoint/recovery 处理这种情况（本阶段不做，
取舍 A6），Avid 用更小的办法：投影时把这批调用整段丢掉，其余消息一条不丢
——包括崩溃之后又续接出来的那些轮次。

丢掉的部分没有信息损失：那一轮本来就没跑完。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .types import BranchScan, Entry, MESSAGE_ENTRY

__all__ = ["messages_for_branch", "entries_to_messages", "repair_incomplete_batches"]


def messages_for_branch(session: Any, branch: str = "main") -> list[dict[str, Any]]:
    """取一条分支上的全部消息（从旧到新）。分支不存在就返回空列表。"""
    found = session.branch(branch)
    if found is None:
        return []
    entries = found.find_entries(BranchScan(order="oldestFirst"))
    return entries_to_messages(entries)


def entries_to_messages(entries: Sequence[Entry]) -> list[dict[str, Any]]:
    """消息条目 → 消息列表，并丢掉不完整的尾巴。"""
    messages = [
        dict(entry.message)
        for entry in entries
        if entry.type == MESSAGE_ENTRY and entry.message is not None
    ]
    return repair_incomplete_batches(messages)


def repair_incomplete_batches(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """让结果能通过 ``Transcript`` 的结构校验：每个 tool_call 都要有结果。

    规则：整批结果没到齐的调用（连同它已经拿到的部分结果）丢掉；孤儿 tool
    结果（没有对应调用）也丢掉；其余消息保持原顺序（返回的是新列表）。
    """
    kept: list[dict[str, Any]] = []
    pending: set[str] = set()
    batch_start: int | None = None

    for message in messages:
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id"))
            if call_id not in pending:
                continue
            pending.discard(call_id)
            kept.append(message)
            continue

        if pending:
            del kept[batch_start:]  # type: ignore[arg-type]
            pending = set()
            batch_start = None

        calls = message.get("tool_calls") or []
        if calls:
            pending = {str(call.get("id")) for call in calls}
            batch_start = len(kept)
        kept.append(message)

    if pending:
        del kept[batch_start:]  # type: ignore[arg-type]
    return kept
