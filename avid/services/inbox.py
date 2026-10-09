"""Session-level Inbox: received inputs that have not been adopted yet.

A delivery is either ``now`` (the earliest possible moment: the active run's next step boundary,
or a fresh run when idle) or ``after`` (the next turn). Accepted input never disappears: an
unclaimed ``now`` is downgraded to ``after`` with ``missed=True``, the server never starts runs
on its own, and claiming is atomic. This is not a second session truth; adoption stays with
``SessionRecorder``, and like ``ApprovalTable`` it is process memory.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from ..agent.events import now_ms

#: The two delivery modes; anything else is rejected (no third, semantically close tier).
MODE_NOW = "now"
MODE_AFTER = "after"
MODES = (MODE_NOW, MODE_AFTER)


@dataclass(frozen=True)
class PendingInput:
    """One queued input; ``content`` is stored form and ``params`` are captured at delivery."""

    input_id: str
    mode: str
    content: Any
    params: dict[str, Any] = field(default_factory=dict)
    client_id: str | None = None
    created_at: int = 0
    #: Downgraded from now (the run ended before it was claimed): the UI reports it as missed.
    missed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Wire form: text preview and image count; bytes come from the session read endpoint."""
        from .. import attachments

        return {
            "input_id": self.input_id,
            "mode": self.mode,
            "text": attachments.render_content_text(self.content),
            "images": attachments.image_count(self.content),
            "client_id": self.client_id,
            "created_at": self.created_at,
            "missed": self.missed,
        }


class SessionInbox:
    """Pending inputs of one session; all methods run under the instance lock."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._lock = threading.RLock()
        self._items: list[PendingInput] = []

    def add(
        self,
        *,
        mode: str,
        content: Any,
        params: dict[str, Any] | None = None,
        client_id: str | None = None,
    ) -> PendingInput:
        """Accept an input; a repeat ``client_id`` returns the existing item (idempotent)."""
        if mode not in MODES:
            raise ValueError(f"mode 只能是 {' / '.join(MODES)}，收到 {mode!r}")
        with self._lock:
            if client_id:
                for existing in self._items:
                    if existing.client_id == client_id:
                        return existing
            item = PendingInput(
                input_id=f"in_{uuid.uuid4().hex[:12]}",
                mode=mode,
                content=content,
                params=dict(params or {}),
                client_id=client_id,
                created_at=now_ms(),
            )
            self._items.append(item)
            return item

    def pending(self) -> list[PendingInput]:
        with self._lock:
            return list(self._items)

    def take_steers(self) -> list[PendingInput]:
        """Take all ``now`` items in delivery order; the run thread calls this at each round."""
        with self._lock:
            taken = [item for item in self._items if item.mode == MODE_NOW]
            self._items = [item for item in self._items if item.mode != MODE_NOW]
            return taken

    def take_for_run(self, input_id: str) -> PendingInput | None:
        """Atomically claim one input to start a run; None when it is gone or already claimed."""
        with self._lock:
            for index, item in enumerate(self._items):
                if item.input_id == input_id:
                    del self._items[index]
                    return item
            return None

    def downgrade_steers(self) -> list[PendingInput]:
        """Run over: unclaimed ``now`` becomes ``after`` with ``missed=True``; still queued."""
        with self._lock:
            downgraded: list[PendingInput] = []
            items: list[PendingInput] = []
            for item in self._items:
                if item.mode == MODE_NOW:
                    item = replace(item, mode=MODE_AFTER, missed=True)
                    downgraded.append(item)
                items.append(item)
            self._items = items
            return downgraded

    def restore(self, item: PendingInput) -> None:
        """Put a just-claimed item back at the head (failed run start): nothing is lost."""
        with self._lock:
            if all(existing.input_id != item.input_id for existing in self._items):
                self._items.insert(0, item)

    def remove(self, input_id: str) -> bool:
        """Drop an unclaimed input; a claimed one returns False (it is a session fact now)."""
        with self._lock:
            for index, item in enumerate(self._items):
                if item.input_id == input_id:
                    del self._items[index]
                    return True
            return False

    def empty(self) -> bool:
        with self._lock:
            return not self._items


__all__ = ["MODE_AFTER", "MODE_NOW", "MODES", "PendingInput", "SessionInbox"]
