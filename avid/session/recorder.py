"""Messages to session entries: each observed message becomes one commit, so a crash never loses a completed turn."""

from __future__ import annotations

import logging
from typing import Any

from .session import SessionBranch, StorageBackedSession
from .types import MESSAGE_ENTRY, EntryType
from .values import DEFAULT_BRANCH, branch_compaction, branch_usage

logger = logging.getLogger("avid.session.recorder")


class SessionRecorder:
    """Persists each message as its own independently committed entry, returning the new entry id."""

    def __init__(
        self, session: StorageBackedSession, branch: str = DEFAULT_BRANCH
    ) -> None:
        self.session = session
        self.branch = branch
        # Entry ids in append order, for callers that need to reference them.
        self.entry_ids: list[str] = []
        self._branch: SessionBranch | None = None

    def ensure_branch(self) -> SessionBranch:
        """Create the branch lazily, because session creation does not establish one implicitly."""
        if self._branch is None:
            found = self.session.branch(self.branch)
            self._branch = found if found is not None else self.session.create_branch(
                self.branch, None
            )
        return self._branch

    def on_message(
        self, message: dict[str, Any], *, entry_type: EntryType = MESSAGE_ENTRY
    ) -> str:
        """Same signature as the notification callback, so it can be passed in directly; returns the new entry id.

        `entry_type` 决定这条消息怎么被读到（消息 / 提醒 / 失败记账），见 types.py 的三个常量。
        """
        branch = self.ensure_branch()
        entry_id = branch.append_message(message, entry_type=entry_type)
        self.entry_ids.append(entry_id)
        logger.debug("会话落库 %s：%s", entry_id, message.get("role"))
        return entry_id

    @property
    def count(self) -> int:
        return len(self.entry_ids)

    def tip_seq(self) -> int | None:
        """当前分支 tip 条目的 seq；分支还没有条目时返回 None（写前快照的落点探针）。"""
        tip = self.ensure_branch().get_tip_id()
        if tip is None:
            return None
        entry = self.session.get_entry(tip)
        return None if entry is None else entry.seq

    def record_usage(self, payload: dict[str, Any]) -> None:
        """Per-branch usage value, same address overwritten each run; usage is not history, so it is not an entry."""
        # Kept out of the entry projection, so usage never becomes history and needs no type exception there.
        self.session.set_value(branch_usage(self.branch), payload)
        logger.debug("会话落库 usage：分支 %s", self.branch)

    def record_compaction(self, summary: dict[str, Any], keep: int = 0) -> None:
        """Per-branch compaction cursor: projection replaces history up to the tip with the summary (+ kept tail).

        锚点是写入时的分支 tip seq——条目只追加不可变，所以 snip/micro 这类只改内存的
        步骤不影响它；摘要消息自带落盘路径，投影端不需要再找原文。
        """
        branch = self.ensure_branch()
        tip = branch.get_tip_id()
        if tip is None:
            return
        entry = self.session.get_entry(tip)
        if entry is None:
            return
        self.session.set_value(
            branch_compaction(self.branch),
            {"through_seq": entry.seq, "summary": summary, "keep": keep},
        )
        logger.info(
            "会话落盘压缩游标：分支 %s through_seq=%d keep=%d", self.branch, entry.seq, keep
        )
