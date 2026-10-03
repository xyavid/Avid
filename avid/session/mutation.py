"""Mutation line: serializes the read-modify-write so a branch head is never rewritten concurrently."""

from __future__ import annotations

import threading

from .errors import SessionBusyError, SessionClosedError


class MutationLine:
    """Holds one mutation job at a time; a callback that re-enters the public write path on the same thread fails fast."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._owner: int | None = None
        self._sealed: SessionClosedError | None = None

    def acquire(self) -> None:
        """Take the gate, blocking while another job holds it; a re-entrant or sealed call raises instead of waiting."""
        me = threading.get_ident()
        with self._condition:
            if self._sealed is not None:
                raise self._sealed
            if self._owner == me:
                raise SessionBusyError(
                    "同一个线程在变更回调里又发起了变更。"
                    "请把这些写合并进同一次 commit，而不要在回调里调用公开写方法。"
                )
            # Condition's wait queue makes waiters acquire the gate in arrival order.
            while self._owner is not None:
                self._condition.wait()
                if self._sealed is not None:
                    raise self._sealed
            self._owner = me

    def release(self) -> None:
        with self._condition:
            self._owner = None
            self._condition.notify_all()

    def held_by_current_thread(self) -> bool:
        with self._condition:
            return self._owner == threading.get_ident()

    def seal(self, error: SessionClosedError) -> None:
        """Seal the line so later acquires fail at once while the job already running still finishes."""
        with self._condition:
            if self._sealed is None:
                self._sealed = error
            self._condition.notify_all()

    @property
    def sealed_error(self) -> SessionClosedError | None:
        """The close error to raise once the line is sealed, or None while it is still open."""
        return self._sealed

    def wait_idle(self) -> None:
        """Block until the job currently holding the gate releases it."""
        with self._condition:
            while self._owner is not None:
                self._condition.wait()
