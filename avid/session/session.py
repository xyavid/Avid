"""Session handle: lifecycle and write ordering for one id, with storage holding the data."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from .. import attachments
from .errors import (
    SessionBranchExistsError,
    SessionBusyError,
    SessionClosedError,
    SessionError,
    SessionInvalidBranchError,
    SessionInvalidMessageError,
    SessionInvariantError,
    SessionUnknownTargetError,
)
from .ids import UuidV7Generator, validate_session_id
from .mutation import MutationLine
from .types import (
    MESSAGE_ENTRY,
    BranchScan,
    CommitResult,
    Entry,
    EntryQuery,
    EntryType,
    EntryWrite,
    IdGenerator,
    NewEntry,
    SessionMetadata,
    SessionStats,
    Storage,
    StoredValue,
    Write,
)
from .values import (
    BRANCH_TIP_NS,
    DEFAULT_BRANCH,
    ValueAddress,
    branch_tip,
    delete_value,
    entry_label,
    session_name,
    set_value,
)

logger = logging.getLogger("avid.session")

# Result type of a mutate callback, so one grant can produce whatever the caller needs.
T = TypeVar("T")

# Roles the session layer accepts; anything else cannot be stored or replayed.
_ALLOWED_ROLES = ("user", "assistant", "tool")


def validate_message(message: Any) -> None:
    """Rejects a message that could not be stored, so both backends accept exactly the same set."""
    if not isinstance(message, dict):
        raise SessionInvalidMessageError(
            f"消息必须是 dict，拿到 {type(message).__name__}"
        )
    role = message.get("role")
    if role not in _ALLOWED_ROLES:
        raise SessionInvalidMessageError(
            f"role 必须是 {'/'.join(_ALLOWED_ROLES)} 之一，拿到 {role!r}"
        )
    if role == "tool" and not isinstance(message.get("tool_call_id"), str):
        raise SessionInvalidMessageError("tool 消息缺 tool_call_id")
    if role == "assistant":
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                raise SessionInvalidMessageError(
                    "assistant 的每个 tool_call 都要有字符串 id"
                )
    # 分块内容（阶段 59）：形状与上限的唯一判据在 attachments——这里是写盘前的最后一道闸，
    # 过了它，日志里的块就一定渲染得出来。
    problems = attachments.check_content(message.get("content"))
    if problems is not None:
        raise SessionInvalidMessageError(f"消息内容不合法：{problems}")
    try:
        json.dumps(message, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise SessionInvalidMessageError(f"消息不是 JSON 可序列化的：{exc}") from exc


class SessionMutation:
    """One exclusive read-modify-write grant: at most one commit, invalid once ended."""

    def __init__(self, storage: Storage, release: Callable[[], None]) -> None:
        self._storage = storage
        self._release = release
        self._active = True
        self._committed = False

    def commit(self, writes: Sequence[Write]) -> CommitResult:
        self._assert_active()
        if self._committed:
            raise SessionError("本次变更已经提交过：一次变更只允许一次 commit。")
        self._committed = True
        return self._storage.commit(writes)

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]:
        self._assert_active()
        return self._storage.get_entries(ids)

    def get_value(self, address: ValueAddress) -> StoredValue | None:
        self._assert_active()
        return self._storage.get_value(address)

    def scan_branch(self, query: BranchScan) -> list[Entry]:
        self._assert_active()
        return self._storage.scan_branch(query)

    def get_stats(self) -> SessionStats:
        self._assert_active()
        return self._storage.get_stats()

    def end(self) -> None:
        if not self._active:
            return
        self._active = False
        self._release()

    def _assert_active(self) -> None:
        if not self._active:
            raise SessionError("这次变更已经结束（end 之后不能再读写）。")


class SessionBranch:
    """One named chain of entries; its tip is the value ``avid.branch.tip.<name>``."""

    def __init__(self, name: str, session: "StorageBackedSession") -> None:
        self.name = name
        self._session = session

    def get_tip_id(self) -> str | None:
        """Tip entry id, or None for an empty branch, which the default branch may legally be."""
        return self._session._branch_tip(self.name)

    def find_entries(self, query: BranchScan | None = None) -> list[Entry]:
        # A query without an explicit start begins at this branch's tip.
        query = query or BranchScan()
        start = query.start if query.start is not None else self.get_tip_id()
        if start is None:
            return []
        return self._session.scan_branch(
            BranchScan(
                start=start,
                type=query.type,
                order=query.order,
                limit=query.limit,
                cursor_seq=query.cursor_seq,
            )
        )

    def find_entry(self, query: BranchScan | None = None) -> Entry | None:
        query = query or BranchScan()
        # Only the first match is returned, so a caller-supplied limit is clamped to one.
        limit = 1 if query.limit is None else min(query.limit, 1)
        found = self.find_entries(
            BranchScan(
                start=query.start,
                type=query.type,
                order=query.order,
                limit=limit,
                cursor_seq=query.cursor_seq,
            )
        )
        return found[0] if found else None

    def append_message(
        self, message: dict[str, Any], *, entry_type: EntryType = MESSAGE_ENTRY
    ) -> str:
        return self._session.append_message(self.name, message, entry_type=entry_type)

    def __repr__(self) -> str:  # pragma: no cover - log readability only
        return f"<SessionBranch {self.name!r}>"


class StorageBackedSession:
    """One open session handle; the repository allows a single handle per id at a time."""

    def __init__(
        self,
        metadata: SessionMetadata,
        storage: Storage,
        *,
        mutation_line: MutationLine | None = None,
        id_generator: IdGenerator | None = None,
        close_storage: bool = True,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        validate_session_id(metadata.id)
        self.metadata = metadata
        self.id_generator = id_generator or UuidV7Generator()
        self._storage = storage
        self._line = mutation_line or MutationLine()
        self._close_storage = close_storage
        self._on_close = on_close
        self._branches: dict[str, SessionBranch] = {}
        self._close_lock = threading.Lock()
        self._state = "open"

    # Reads: every accessor checks that the handle is still open.

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]:
        self._assert_open()
        return self._storage.get_entries(ids)

    def get_entry(self, entry_id: str) -> Entry | None:
        return self.get_entries([entry_id]).get(entry_id)

    def get_value(self, address: ValueAddress) -> StoredValue | None:
        self._assert_open()
        return self._storage.get_value(address)

    def scan_values(self, namespace: str) -> list[StoredValue]:
        self._assert_open()
        return self._storage.scan_values(namespace)

    def scan_branch(self, query: BranchScan) -> list[Entry]:
        self._assert_open()
        return self._storage.scan_branch(query)

    def get_stats(self) -> SessionStats:
        self._assert_open()
        return self._storage.get_stats()

    def get_name(self) -> str | None:
        stored = self.get_value(session_name())
        return None if stored is None else stored.value

    def get_label(self, target_id: str) -> str | None:
        stored = self.get_value(entry_label(target_id))
        return None if stored is None else stored.value

    def find_entries(self, query: EntryQuery | None = None) -> list[Entry]:
        query = query or EntryQuery()
        self._assert_open()
        return self._storage.scan_entries(query)

    def find_entry(self, query: EntryQuery | None = None) -> Entry | None:
        query = query or EntryQuery()
        # Only the first match is returned, so a caller-supplied limit is clamped to one.
        limit = 1 if query.limit is None else min(query.limit, 1)
        found = self.find_entries(
            EntryQuery(
                type=query.type,
                order=query.order,
                limit=limit,
                cursor_seq=query.cursor_seq,
            )
        )
        return found[0] if found else None

    # Branches: a branch object is a view onto one named chain.

    def branch_names(self) -> list[str]:
        """Branch names that hold a value, with the default branch always first."""
        self._assert_open()
        stored = [item.key for item in self.scan_values(BRANCH_TIP_NS)]
        return [DEFAULT_BRANCH, *(name for name in stored if name != DEFAULT_BRANCH)]

    def branch(self, name: str) -> SessionBranch | None:
        """Returns a branch view; the default branch is implicit and may be empty."""
        self._assert_open()
        self._assert_valid_branch(name)
        if self._storage.get_value(branch_tip(name)) is None and name != DEFAULT_BRANCH:
            return None
        return self._branch_object(name)

    def create_branch(self, name: str, at: str | None = None) -> SessionBranch:
        """Creates a branch, refusing a name that already exists so no chain is silently dropped."""
        self._assert_open()
        self._assert_valid_branch(name)

        def job(mutator: SessionMutation) -> None:
            if mutator.get_value(branch_tip(name)) is not None:
                raise SessionBranchExistsError(name)
            if at is not None and at not in mutator.get_entries([at]):
                raise SessionUnknownTargetError(at)
            mutator.commit([set_value(branch_tip(name), at)])

        self.mutate(job)
        return self._branch_object(name)

    def append_message(
        self,
        branch: str,
        message: dict[str, Any],
        *,
        entry_type: EntryType = MESSAGE_ENTRY,
    ) -> str:
        """Appends a message at the branch tip, returning the entry id; the tip commits with it."""
        self._assert_open()
        validate_message(message)
        entry_id = self.id_generator.next()

        def job(mutator: SessionMutation) -> None:
            tip = mutator.get_value(branch_tip(branch))
            if tip is None and branch != DEFAULT_BRANCH:
                # A non-default branch must exist first: a typo must not silently start a new chain.
                raise SessionInvariantError(
                    f"未知分支：{branch}（先 create_branch 建它再写入）"
                )
            # On a branch with no tip value yet, the first entry has no parent.
            parent_id = None if tip is None else tip.value
            # Entry and tip share one commit, so a branch never points at a missing entry.
            mutator.commit(
                [
                    EntryWrite(
                        NewEntry(
                            id=entry_id,
                            parent_id=parent_id,
                            type=entry_type,
                            message=message,
                        )
                    ),
                    set_value(branch_tip(branch), entry_id),
                ]
            )

        self.mutate(job)
        return entry_id

    # Writes: every write goes through one mutation grant.

    def begin_mutation(self) -> SessionMutation:
        """Takes one exclusive write grant; nesting it on one thread raises SessionBusyError."""
        self._assert_open()
        self._line.acquire()
        try:
            self._assert_open()
        except BaseException:
            self._line.release()
            raise
        return SessionMutation(self._storage, self._line.release)

    def mutate(self, callback: Callable[[SessionMutation], T]) -> T:
        mutator = self.begin_mutation()
        try:
            return callback(mutator)
        finally:
            mutator.end()

    def set_value(self, address: ValueAddress, value: Any) -> None:
        self.mutate(lambda mutator: mutator.commit([set_value(address, value)]))

    def delete_value(self, address: ValueAddress) -> None:
        self.mutate(lambda mutator: mutator.commit([delete_value(address)]))

    def set_name(self, name: str | None) -> None:
        if name is None:
            self.delete_value(session_name())
        else:
            self.set_value(session_name(), name)

    def set_label(self, target_id: str, label: str | None) -> None:
        address = entry_label(target_id)
        if label is None:
            self.delete_value(address)
        else:
            self.set_value(address, label)

    # Lifecycle: one close per handle, and no job may be running during it.

    def close(self) -> None:
        """Closes the handle: refuse new work, wait for the running job, then close the storage."""
        with self._close_lock:
            if self._state != "open":
                return
            if self._line.held_by_current_thread():
                raise SessionBusyError(
                    "不能在变更回调里关闭会话：先让 mutate 结束，再 close。"
                )
            self._state = "closing"
            self._line.seal(SessionClosedError("会话已关闭，不再接受读写。"))
            self._line.wait_idle()
            try:
                if self._close_storage:
                    self._storage.close()
            finally:
                self._state = "closed"
                if self._on_close is not None:
                    self._on_close()
                logger.debug("会话已关闭：%s", self.metadata.id)

    @property
    def closed(self) -> bool:
        return self._state != "open"

    # Internals: branch resolution and open-state checks.

    def _branch_tip(self, name: str, *, required: bool = False) -> str | None:
        # required makes a missing tip an error; the default branch may legally have none.
        self._assert_valid_branch(name)
        stored = self._storage.get_value(branch_tip(name))
        if stored is None:
            if required:
                raise SessionInvariantError(f"未知分支：{name}")
            return None
        return stored.value

    def _branch_object(self, name: str) -> SessionBranch:
        # Branch views are cached per name so one handle keeps returning the same object.
        branch = self._branches.get(name)
        if branch is None:
            branch = SessionBranch(name, self)
            self._branches[name] = branch
        return branch

    @staticmethod
    def _assert_valid_branch(name: str) -> None:
        if not name:
            raise SessionInvalidBranchError(name, "分支名不能为空")
        if "\u0000" in name:
            raise SessionInvalidBranchError(name, "分支名不能含 NUL")

    def _assert_open(self) -> None:
        if self._state != "open":
            raise SessionClosedError(f"会话已关闭：{self.metadata.id}")
