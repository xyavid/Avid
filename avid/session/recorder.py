"""Messages to session entries: each observed message becomes one commit, so a crash never loses a completed turn."""

from __future__ import annotations

import logging
from typing import Any

from .session import SessionBranch, StorageBackedSession
from .types import MESSAGE_ENTRY, NOTICE_ENTRY
from .values import DEFAULT_BRANCH, branch_usage

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

    def on_message(self, message: dict[str, Any], *, notice: bool = False) -> str:
        """Same signature as the notification callback, so it can be passed in directly; returns the new entry id."""
        branch = self.ensure_branch()
        entry_id = branch.append_message(
            message, entry_type=NOTICE_ENTRY if notice else MESSAGE_ENTRY
        )
        self.entry_ids.append(entry_id)
        logger.debug("会话落库 %s：%s", entry_id, message.get("role"))
        return entry_id

    @property
    def count(self) -> int:
        return len(self.entry_ids)

    def record_usage(self, payload: dict[str, Any]) -> None:
        """Per-branch usage value, same address overwritten each run; usage is not history, so it is not an entry."""
        # Kept out of the entry projection, so usage never becomes history and needs no type exception there.
        self.session.set_value(branch_usage(self.branch), payload)
        logger.debug("会话落库 usage：分支 %s", self.branch)
