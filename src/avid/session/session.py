"""会话句柄：一条 id 对应一个 ``StorageBackedSession``。

它负责的是**生命周期与写入顺序**：句柄什么时候还能用（open → closing → closed）、
分支头与条目怎么同事务写、谁在什么时候能改状态。真正的数据落点交给 ``Storage``：
内存后端是一份 ``SessionState``，文件后端是追加的 JSONL。本模块因此不认识
路径、文件或 JSON——它只认识条目与值。

与参考实现的三处对应关系：

* ``SessionMutation`` 对应 pi 的 ``SessionMutation``：一次变更恰好提交一次，
  ``end`` 之后能力作废。
* ``append_message`` 对应 ``Branch.appendMessage``：读分支头 → 写条目 →
  写新头，三步一个事务。
* ``close`` 对应 pi 的 ``close``：先 seal 变更线（排队者立刻失败），再 drain
  正在跑的作业，最后让存储关闭。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

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
    BranchScan,
    CommitResult,
    Entry,
    EntryQuery,
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

T = TypeVar("T")

_ALLOWED_ROLES = ("user", "assistant", "tool")


def validate_message(message: Any) -> None:
    """在写入前拦住不可能落库的消息。

    这不是 ``Transcript`` 的结构校验（那个管 tool_call 与结果的配对，属于一份
    完整上下文）；这里只保证"这一条能安全地变成 JSON，并且两个后端对它的接受
    程度完全一样"。所以校验放在会话层，而不是让 JSONL 后端靠 ``json.dumps``
    兜底——否则内存后端会收下 JSONL 后端拒绝的东西，两个后端就分叉了。
    """
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
    try:
        json.dumps(message, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise SessionInvalidMessageError(f"消息不是 JSON 可序列化的：{exc}") from exc


class SessionMutation:
    """一次独占的读-改-写能力。

    ``commit`` 至多一次：第二次调用直接报错，而不是把同一次作业写两遍。
    ``end`` 之后能力作废——防止回调返回后还拿着它写。
    """

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
    """一条命名的条目链。链尾是值 ``avid.branch.tip.<name>``。"""

    def __init__(self, name: str, session: "StorageBackedSession") -> None:
        self.name = name
        self._session = session

    def get_tip_id(self) -> str | None:
        """链尾条目 id；空分支（含还没写过的默认分支）返回 None。

        不再 `required=True`：默认分支是隐式存在的空链，把"没有链尾值"当成错误会
        让"空 main"这个合法状态变成异常。非默认分支的对象只能由 `create_branch`
        或 `branch()`（要求存在）拿到，所以这里也不需要额外校验。
        """
        return self._session._branch_tip(self.name)

    def find_entries(self, query: BranchScan | None = None) -> list[Entry]:
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

    def append_message(self, message: dict[str, Any]) -> str:
        return self._session.append_message(self.name, message)

    def __repr__(self) -> str:  # pragma: no cover - 只为日志可读
        return f"<SessionBranch {self.name!r}>"


class StorageBackedSession:
    """一个打开的会话句柄。同名 id 同时只允许一个（由仓库保证）。"""

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

    # ---------------- 读 ----------------

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

    # ---------------- 分支 ----------------

    def branch_names(self) -> list[str]:
        """有值的分支名（按建分支的先后），默认分支永远排在第一个。

        分支头就是 ``BRANCH_TIP_NS`` 下的一组值，所以「有哪些分支」只能靠枚举这个
        命名空间回答。新建会话在第一次落库之前没有任何分支值，但读侧必须把 main
        视作隐式默认——否则新会话会显示成「没有分支」，而它随时可以往 main 写。
        """
        self._assert_open()
        stored = [item.key for item in self.scan_values(BRANCH_TIP_NS)]
        return [DEFAULT_BRANCH, *(name for name in stored if name != DEFAULT_BRANCH)]

    def branch(self, name: str) -> SessionBranch | None:
        """取一条分支。默认分支**隐式存在**（可能为空），其余分支没建过就是 None。

        以前这里对空会话返回 None，而 `branch_names()` 同时宣称有 main——同一个问题
        两个答案。空 main 是合法状态（第一条消息的 parent 是 None），所以给它一个
        空分支对象，与 `branch_names()`、`messages_for_branch()` 的说法一致。
        """
        self._assert_open()
        self._assert_valid_branch(name)
        if self._storage.get_value(branch_tip(name)) is None and name != DEFAULT_BRANCH:
            return None
        return self._branch_object(name)

    def create_branch(self, name: str, at: str | None = None) -> SessionBranch:
        """建一条分支。已存在就报错——悄悄重建会把原来那条链丢掉。"""
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

    def append_message(self, branch: str, message: dict[str, Any]) -> str:
        """往分支尾追加一条消息，返回条目 id。分支头与条目同一次提交。"""
        self._assert_open()
        validate_message(message)
        entry_id = self.id_generator.next()

        def job(mutator: SessionMutation) -> None:
            tip = mutator.get_value(branch_tip(branch))
            if tip is None and branch != DEFAULT_BRANCH:
                # 非默认分支必须显式建过：名字敲错时不该悄悄建一条新链。
                raise SessionInvariantError(
                    f"未知分支：{branch}（先 create_branch 建它再写入）"
                )
            # 默认分支还没有分支头值时，第一条消息的 parent 就是 None。
            parent_id = None if tip is None else tip.value
            mutator.commit(
                [
                    EntryWrite(
                        NewEntry(
                            id=entry_id,
                            parent_id=parent_id,
                            message=message,
                        )
                    ),
                    set_value(branch_tip(branch), entry_id),
                ]
            )

        self.mutate(job)
        return entry_id

    # ---------------- 写 ----------------

    def begin_mutation(self) -> SessionMutation:
        """拿一次独占的写入能力。同一线程嵌套会报 ``SessionBusyError``。"""
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

    # ---------------- 生命周期 ----------------

    def close(self) -> None:
        """关句柄：先拒绝新作业，再等正在跑的作业结束。"""
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

    # ---------------- 内部 ----------------

    def _branch_tip(self, name: str, *, required: bool = False) -> str | None:
        self._assert_valid_branch(name)
        stored = self._storage.get_value(branch_tip(name))
        if stored is None:
            if required:
                raise SessionInvariantError(f"未知分支：{name}")
            return None
        return stored.value

    def _branch_object(self, name: str) -> SessionBranch:
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
