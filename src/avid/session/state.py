"""物化状态：两个后端共用的那一份"会话现在长什么样"。

内存后端直接拿它当存储；文件后端在 open 时把事务行重放进来，在 commit 时
先在这里分配 seq、校验，再写文件。**校验只在这里写一次**，所以两个后端对
"什么算合法提交"的判断不可能分叉（一致性套件跑的就是这一点）。

提交的顺序是有讲究的：先 ``prepare_commit`` 分配 + 校验，再由调用方落盘，
最后才 ``apply``。任何一步失败，状态都还停在提交前——这就是不变量 I2
"全有或全无"的实现位置。
"""

from __future__ import annotations

from collections.abc import Sequence

from .errors import SessionInvariantError
from .types import (
    MESSAGE_ENTRY,
    BranchScan,
    CommittedEntry,
    CommittedValueDelete,
    CommittedValueSet,
    CommittedWrite,
    Entry,
    EntryQuery,
    EntryWrite,
    PreparedCommit,
    SessionStats,
    StoredValue,
    ValueDeleteWrite,
    ValueSetWrite,
    Write,
)
from .values import ValueAddress


class SessionState:
    """条目的树 + 值的表 + 统计，全部物化在内存里。"""

    def __init__(self, next_seq: int = 1) -> None:
        self._entries: dict[str, Entry] = {}
        self._by_seq: list[Entry] = []
        self._values: dict[tuple[str, str], StoredValue] = {}
        self._message_count = 0
        self._next_seq = next_seq

    # ---------- 提交 ----------

    def prepare_commit(self, writes: Sequence[Write], timestamp: int) -> PreparedCommit:
        """分配连续 seq 与同一个 timestamp，并校验；不改动任何状态。"""
        committed: list[CommittedWrite] = []
        for offset, write in enumerate(writes):
            seq = self._next_seq + offset
            if isinstance(write, EntryWrite):
                committed.append(CommittedEntry(seq, timestamp, write.entry))
            elif isinstance(write, ValueSetWrite):
                committed.append(CommittedValueSet(seq, write.namespace, write.key, write.value))
            elif isinstance(write, ValueDeleteWrite):
                committed.append(CommittedValueDelete(seq, write.namespace, write.key))
            else:  # pragma: no cover - 只有开发者会构造出别的类型
                raise SessionInvariantError(f"未知的写入类型：{type(write).__name__}")
        self.validate(committed)
        return PreparedCommit(
            writes=tuple(committed),
            first_seq=self._next_seq,
            seqs=tuple(item.seq for item in committed),
            timestamp=timestamp,
        )

    def validate(self, writes: Sequence[CommittedWrite]) -> None:
        """落盘前 / 重放时的同一套校验：seq 单调、id 不重复、parent 必须存在。"""
        previous_seq = self._next_seq - 1
        seen_ids: set[str] = set()
        for write in writes:
            if write.seq <= previous_seq:
                raise SessionInvariantError(
                    f"存储 seq 非单调：{write.seq} 不大于 {previous_seq}"
                )
            previous_seq = write.seq
            if not isinstance(write, CommittedEntry):
                continue
            entry = write.entry
            if entry.id in self._entries or entry.id in seen_ids:
                raise SessionInvariantError(f"条目 id 重复：{entry.id}")
            if (
                entry.parent_id is not None
                and entry.parent_id not in self._entries
                and entry.parent_id not in seen_ids
            ):
                raise SessionInvariantError(
                    f"条目的 parent 不存在：{entry.parent_id}"
                )
            seen_ids.add(entry.id)

    def apply(self, writes: Sequence[CommittedWrite]) -> SessionStats:
        """把已经校验过的写入落地，返回落地后的统计。"""
        for write in writes:
            if isinstance(write, CommittedEntry):
                entry = Entry(
                    id=write.entry.id,
                    parent_id=write.entry.parent_id,
                    seq=write.seq,
                    timestamp=write.timestamp,
                    type=write.entry.type,
                    message=write.entry.message,
                )
                self._entries[entry.id] = entry
                self._by_seq.append(entry)
                if entry.type == MESSAGE_ENTRY:
                    self._message_count += 1
            elif isinstance(write, CommittedValueSet):
                self._values[(write.namespace, write.key)] = StoredValue(
                    namespace=write.namespace,
                    key=write.key,
                    value=write.value,
                    seq=write.seq,
                )
            else:
                self._values.pop((write.namespace, write.key), None)
            self._next_seq = write.seq + 1
        return self.stats

    def advance_next_seq(self, next_seq: int) -> None:
        """header 里的高水位：只有重放后仍更大时才采用（快照重写的兼容位）。"""
        if not isinstance(next_seq, int) or next_seq < 1:
            raise SessionInvariantError(f"非法的 seq 高水位：{next_seq!r}")
        self._next_seq = max(self._next_seq, next_seq)

    # ---------- 读 ----------

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @property
    def stats(self) -> SessionStats:
        return SessionStats(message_count=self._message_count)

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]:
        found: dict[str, Entry] = {}
        for entry_id in ids:
            entry = self._entries.get(entry_id)
            if entry is not None:
                found[entry_id] = entry
        return found

    def get_value(self, address: ValueAddress) -> StoredValue | None:
        return self._values.get((address.namespace, address.key))

    def values_in(self, namespace: str) -> list[StoredValue]:
        """某个 namespace 下的全部值，按 seq 升序。

        只做命名空间级枚举，不做 prefix 扫描：分支列表是它的第一个使用者——分支头
        就是 ``BRANCH_TIP_NS`` 下的一组值，除此之外没有别的办法回答「有哪些分支」。
        """
        found = [item for item in self._values.values() if item.namespace == namespace]
        found.sort(key=lambda item: item.seq)
        return found

    def scan_branch(self, query: BranchScan) -> list[Entry]:
        """从 ``start`` 沿 parent_id 往回走，得到这条链。"""
        if query.start is None:
            raise SessionInvariantError("scan_branch 需要一个起点条目 id")
        start = self._entries.get(query.start)
        if start is None:
            raise SessionInvariantError(f"分支起点不存在：{query.start}")

        path: list[Entry] = []
        cursor: Entry | None = start
        while cursor is not None:
            path.append(cursor)
            if cursor.parent_id is None:
                break
            parent = self._entries.get(cursor.parent_id)
            if parent is None:
                raise SessionInvariantError(
                    f"链断了：{cursor.id} 的 parent {cursor.parent_id} 不存在"
                )
            cursor = parent
        if query.order == "oldestFirst":
            path.reverse()

        filtered = [
            item
            for item in path
            if (query.type is None or item.type == query.type)
            and (
                query.cursor_seq is None
                or (
                    item.seq > query.cursor_seq
                    if query.order == "oldestFirst"
                    else item.seq < query.cursor_seq
                )
            )
        ]
        return filtered if query.limit is None else filtered[: max(0, query.limit)]

    def scan_entries(self, query: EntryQuery) -> list[Entry]:
        """按 seq 全局扫描。默认从新到旧。"""
        limit = float("inf") if query.limit is None else max(0, query.limit)
        descending = query.order == "desc"
        found: list[Entry] = []
        items = reversed(self._by_seq) if descending else iter(self._by_seq)
        for entry in items:
            if query.type is not None and entry.type != query.type:
                continue
            if query.cursor_seq is not None:
                # cursor 是**排他**的：它指向上一页的最后一条，下一页从它之后开始。
                if descending and entry.seq >= query.cursor_seq:
                    continue
                if not descending and entry.seq <= query.cursor_seq:
                    continue
            found.append(entry)
            if len(found) >= limit:
                break
        return found
