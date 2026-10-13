"""Messages to session entries: each observed message becomes one commit, so a crash never loses a completed turn."""

from __future__ import annotations

import logging
from typing import Any

from .session import SessionBranch, StorageBackedSession
from .types import MESSAGE_ENTRY, EntryType
from .values import DEFAULT_BRANCH, branch_compaction, branch_plan, branch_usage

logger = logging.getLogger("avid.session.recorder")

#: The plan rides inside an object: a bare list is not a value shape here, and the wrapper leaves
#: room for a field beside the items.
PLAN_ITEMS = "items"


def stored_plan(session: Any, branch: str = DEFAULT_BRANCH) -> list[Any]:
    """Read one branch's plan back item by item; anything that is not the declared shape reads as
    no plan.

    Item validity is the agent's business — its list drops what it cannot use, and says so — so
    this only unwraps the value, and a corrupted slot never keeps a run from starting.
    """
    stored = session.get_value(branch_plan(branch))
    if stored is None:
        return []
    value = stored.value
    raw = value.get(PLAN_ITEMS) if isinstance(value, dict) else None
    if not isinstance(raw, list):
        logger.warning("计划值形状不对，按没有计划处理：%r", value)
        return []
    return list(raw)


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
        """Same signature as the notification callback, so it can be passed straight in;
        returns the new entry id.
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
        """Seq of the branch tip entry, or None while the branch has no entries."""
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

    def record_plan(self, items: list[dict[str, Any]]) -> None:
        """Per-branch plan value, written whole and removed once nothing is left to do.

        A finished plan is not history to keep — the list exists to be worked off — so an empty or
        all-completed list deletes the value instead of storing an empty one.
        """
        if not items or all(item.get("status") == "completed" for item in items):
            self.session.delete_value(branch_plan(self.branch))
            logger.debug("会话删除计划：分支 %s", self.branch)
            return
        self.session.set_value(branch_plan(self.branch), {PLAN_ITEMS: list(items)})
        logger.info("会话落盘计划：分支 %s（%d 项）", self.branch, len(items))

    def record_compaction(self, summary: dict[str, Any], keep: int = 0) -> None:
        """Per-branch compaction cursor: projection replaces history up to the tip with the summary
        plus kept tail.
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
