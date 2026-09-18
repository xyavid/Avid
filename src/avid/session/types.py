"""会话的数据结构与协议。

命名对照参考实现（``earendil-works/pi`` 的 ``harness/session``）：

* 文件格式里用 camelCase（``parentId`` / ``storageVersion`` / ``createdAt``），
  与 pi 的 JSONL 同形；Python 侧一律 snake_case。
* ``Entry`` 只保留 ``message`` 一种。pi 的 ``compaction`` / ``branch_summary`` /
  ``custom`` 三种要等各自的真实写入者出现（见阶段 12 的取舍 A7/A8）。
* pi 给每个方法都传一个宿主 ``Context``；Avid 没有这层抽象，能力（根目录、
  时钟、id 生成器）在构造期注入，因此下面所有签名都没有 context 参数。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar

if TYPE_CHECKING:  # 仅用于标注：运行时由 values.py 提供，避免循环导入
    from .values import ValueAddress

T = TypeVar("T")

# 条目判别字段。现在只有一种；格式版本号（storage_version）为新类型留位。
EntryType = str
MESSAGE_ENTRY: EntryType = "message"

Order = Literal["asc", "desc"]
BranchOrder = Literal["newestFirst", "oldestFirst"]

# 存储格式版本。读到别的版本要显式报错，而不是猜着读。
STORAGE_VERSION = 1


@dataclass(frozen=True)
class Entry:
    """一条已经提交的会话条目。

    ``parent_id`` 把条目串成链：分支头只是"链尾是谁"的一个值，
    链本身由 parent 关系还原。提交后视为不可变——不变量 I1 由存储层守护。
    """

    id: str
    parent_id: str | None
    seq: int
    timestamp: int
    type: EntryType
    message: dict[str, Any] | None = None


@dataclass(frozen=True)
class NewEntry:
    """尚未提交的条目：seq 与 timestamp 由提交时统一分配。"""

    id: str
    parent_id: str | None
    type: EntryType = MESSAGE_ENTRY
    message: dict[str, Any] | None = None


@dataclass(frozen=True)
class EntryWrite:
    entry: NewEntry


@dataclass(frozen=True)
class ValueSetWrite:
    namespace: str
    key: str
    value: Any


@dataclass(frozen=True)
class ValueDeleteWrite:
    namespace: str
    key: str


Write = EntryWrite | ValueSetWrite | ValueDeleteWrite

# ---- 提交后形态：seq / timestamp 已分配，是落盘与重放的单位 ----


@dataclass(frozen=True)
class CommittedEntry:
    seq: int
    timestamp: int
    entry: NewEntry


@dataclass(frozen=True)
class CommittedValueSet:
    seq: int
    namespace: str
    key: str
    value: Any


@dataclass(frozen=True)
class CommittedValueDelete:
    seq: int
    namespace: str
    key: str


CommittedWrite = CommittedEntry | CommittedValueSet | CommittedValueDelete


@dataclass(frozen=True)
class StoredValue:
    namespace: str
    key: str
    value: Any
    seq: int


@dataclass(frozen=True)
class EntryQuery:
    """会话级查询：按 seq 全局扫描，默认从新到旧。"""

    type: EntryType | None = None
    order: Order = "desc"
    limit: int | None = None
    cursor_seq: int | None = None


@dataclass(frozen=True)
class BranchScan:
    """分支扫描：``start`` 是链尾，沿 parent_id 往回走。

    ``start`` 允许为空——那是会话级调用（"从分支头开始"）；存储层拿到空 start
    会报错，因为存储只认具体条目。
    """

    start: str | None = None
    type: EntryType | None = None
    order: BranchOrder = "newestFirst"
    limit: int | None = None
    cursor_seq: int | None = None


@dataclass(frozen=True)
class SessionStats:
    """会话统计。pi 这里还有 usage 台账，Avid 的 token 用量目前不落盘（取舍 A3）。"""

    message_count: int


@dataclass(frozen=True)
class CommitResult:
    """一次提交的结果。``stats`` 是**落地之后**的统计。"""

    first_seq: int
    seqs: tuple[int, ...]
    timestamp: int
    stats: SessionStats


@dataclass(frozen=True)
class PreparedCommit:
    """已分配 seq / timestamp 并通过校验、但还没落地的提交。"""

    writes: tuple[CommittedWrite, ...]
    first_seq: int
    seqs: tuple[int, ...]
    timestamp: int


@dataclass(frozen=True)
class SessionMetadata:
    """会话元信息。列表只需要它就够，不必把会话读进来。"""

    id: str
    created_at: int
    storage_version: int = STORAGE_VERSION
    parent_session_id: str | None = None


@dataclass(frozen=True)
class JsonlSessionMetadata(SessionMetadata):
    """文件后端额外暴露的东西：路径与修改时间，供列表与删除定位。"""

    path: Path = field(default_factory=Path)
    modified_at: int = 0


class IdGenerator(Protocol):
    def next(self) -> str: ...


class Storage(Protocol):
    """会话数据面的最小契约。内存后端与文件后端实现同一组方法。"""

    def commit(self, writes: Sequence[Write]) -> CommitResult: ...

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_value(self, address: ValueAddress) -> StoredValue | None: ...

    def scan_values(self, namespace: str) -> list[StoredValue]: ...

    def scan_branch(self, query: BranchScan) -> list[Entry]: ...

    def scan_entries(self, query: EntryQuery) -> list[Entry]: ...

    def get_stats(self) -> SessionStats: ...

    def close(self) -> None: ...


class Branch(Protocol):
    name: str

    def get_tip_id(self) -> str | None: ...

    def find_entries(self, query: BranchScan | None = None) -> list[Entry]: ...

    def find_entry(self, query: BranchScan | None = None) -> Entry | None: ...

    def append_message(self, message: dict[str, Any]) -> str: ...


class Session(Protocol):
    metadata: SessionMetadata
    id_generator: IdGenerator

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_entry(self, entry_id: str) -> Entry | None: ...

    def get_value(self, address: ValueAddress) -> StoredValue | None: ...

    def scan_values(self, namespace: str) -> list[StoredValue]: ...

    def get_stats(self) -> SessionStats: ...

    def find_entries(self, query: EntryQuery | None = None) -> list[Entry]: ...

    def find_entry(self, query: EntryQuery | None = None) -> Entry | None: ...

    def branch(self, name: str) -> Branch | None: ...

    def branch_names(self) -> list[str]: ...

    def create_branch(self, name: str, at: str | None = None) -> Branch: ...

    def begin_mutation(self) -> SessionMutation: ...

    def mutate(self, callback: MutationCallback[T]) -> T: ...

    def set_value(self, address: ValueAddress, value: Any) -> None: ...

    def delete_value(self, address: ValueAddress) -> None: ...

    def get_name(self) -> str | None: ...

    def set_name(self, name: str | None) -> None: ...

    def get_label(self, target_id: str) -> str | None: ...

    def set_label(self, target_id: str, label: str | None) -> None: ...

    def close(self) -> None: ...


class SessionRepo(Protocol):
    def create(
        self, *, id: str | None = None, parent_session_id: str | None = None
    ) -> Session: ...

    def open(self, metadata: SessionMetadata) -> Session: ...

    def list(self) -> list[SessionMetadata]: ...

    def delete(self, metadata: SessionMetadata) -> None: ...

    def close(self) -> None: ...


class SessionMutation(Protocol):
    """一次独占的读-改-写能力：commit 至多一次，end 之后一切失效。"""

    def commit(self, writes: Sequence[Write]) -> CommitResult: ...

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_value(self, address: ValueAddress) -> StoredValue | None: ...

    def scan_branch(self, query: BranchScan) -> list[Entry]: ...

    def get_stats(self) -> SessionStats: ...

    def end(self) -> None: ...


MutationCallback = Callable[[SessionMutation], T]
