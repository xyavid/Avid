"""文件后端：首行 header + 每次提交一行 JSON 事务。

格式与 pi 的 JSONL 存储同形（camelCase 字段、单写裸对象 / 多写数组、
header 里的高水位），差别只在存放位置：pi 用全局根目录加 ``--cwd--`` 编码，
Avid 放在工作区内的 ``.avid/sessions/``（取舍 A2）——工作区边界已经由
``tools/workspace.py`` 定义，而且阶段 8 的教训是落盘必须在工作区内，
否则工具读不回来。

三条关键行为：

* **追加即提交**：一行 = 一次提交，写完 flush + fsync 才返回。中途被杀最多
  丢掉最后一个事务，之前的记录永远有效（不变量 I1/I2）。
* **重放**：open 时把完整行逐条重放回 ``SessionState``，用同一套校验函数，
  所以"运行时接受的"和"重放时接受的"不可能分叉。
* **撕裂行**：文件末尾没有换行的那半行被忽略，并在下次打开时原子重写掉——
  它是崩溃留下的残片，不是数据。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .errors import (
    SessionAlreadyOpenError,
    SessionClosedError,
    SessionExistsError,
    SessionNotFoundError,
    SessionStorageError,
)
from .ids import UuidV7Generator, now_ms, validate_session_id
from .session import StorageBackedSession
from .state import SessionState
from .types import (
    BranchScan,
    CommitResult,
    CommittedEntry,
    CommittedValueDelete,
    CommittedValueSet,
    CommittedWrite,
    Entry,
    EntryQuery,
    IdGenerator,
    JsonlSessionMetadata,
    NewEntry,
    SessionMetadata,
    SessionStats,
    STORAGE_VERSION,
    StoredValue,
    Write,
)
from .values import ValueAddress

logger = logging.getLogger("avid.session.jsonl")

FORMAT_VERSION = 1
HEADER_KIND = "header"
SUFFIX = ".jsonl"


# ---------------- 编解码 ----------------


@dataclass(frozen=True)
class JsonlHeader:
    id: str
    storage_version: int
    created_at: int
    parent_session_id: str | None = None
    next_seq: int | None = None
    # 归属工作区（阶段 18）。老会话没有这个键：读回为 None，由文件位置派生。
    workspace: str | None = None


def encode_header(header: JsonlHeader) -> str:
    record: dict[str, object] = {
        "v": FORMAT_VERSION,
        "kind": HEADER_KIND,
        "id": header.id,
        "storageVersion": header.storage_version,
        "createdAt": header.created_at,
    }
    if header.parent_session_id is not None:
        record["parentSessionId"] = header.parent_session_id
    if header.next_seq is not None:
        record["nextSeq"] = header.next_seq
    if header.workspace is not None:
        record["workspaceId"] = header.workspace
    return json.dumps(record, ensure_ascii=False)


def parse_header(line: str) -> JsonlHeader:
    try:
        record = json.loads(line)
    except ValueError as exc:
        raise SessionStorageError(f"会话 header 不是合法 JSON：{exc}") from exc
    if not isinstance(record, dict):
        raise SessionStorageError("会话 header 必须是 JSON 对象")
    if record.get("kind") != HEADER_KIND:
        raise SessionStorageError(f"会话 header 的 kind 不是 {HEADER_KIND!r}")
    if record.get("v") != FORMAT_VERSION:
        raise SessionStorageError(
            f"会话格式版本不认识：{record.get('v')!r}（本程序只认 {FORMAT_VERSION}）"
        )
    session_id = record.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise SessionStorageError("会话 header 缺 id")
    storage_version = record.get("storageVersion")
    if not isinstance(storage_version, int) or storage_version < 1:
        raise SessionStorageError("会话 header 的 storageVersion 非法")
    created_at = record.get("createdAt")
    if not isinstance(created_at, int) or created_at < 0:
        raise SessionStorageError("会话 header 的 createdAt 非法")
    parent = record.get("parentSessionId")
    if parent is not None and not isinstance(parent, str):
        raise SessionStorageError("会话 header 的 parentSessionId 非法")
    next_seq = record.get("nextSeq")
    if next_seq is not None and (not isinstance(next_seq, int) or next_seq < 1):
        raise SessionStorageError("会话 header 的 nextSeq 非法")
    workspace = record.get("workspaceId")
    if workspace is not None and (not isinstance(workspace, str) or not workspace):
        raise SessionStorageError("会话 header 的 workspaceId 非法")
    return JsonlHeader(
        session_id, storage_version, created_at, parent, next_seq, workspace
    )


def encode_write(write: CommittedWrite) -> dict[str, object]:
    if isinstance(write, CommittedEntry):
        entry = write.entry
        record: dict[str, object] = {
            "kind": "entry",
            "seq": write.seq,
            "timestamp": write.timestamp,
            "id": entry.id,
            "parentId": entry.parent_id,
            "type": entry.type,
        }
        if entry.message is not None:
            record["message"] = entry.message
        return record
    if isinstance(write, CommittedValueSet):
        return {
            "kind": "value",
            "op": "set",
            "seq": write.seq,
            "namespace": write.namespace,
            "key": write.key,
            "value": write.value,
        }
    return {
        "kind": "value",
        "op": "delete",
        "seq": write.seq,
        "namespace": write.namespace,
        "key": write.key,
    }


def decode_write(record: object) -> CommittedWrite:
    if not isinstance(record, dict):
        raise SessionStorageError("事务里的每一写都必须是 JSON 对象")
    seq = record.get("seq")
    if not isinstance(seq, int) or seq < 1:
        raise SessionStorageError("事务缺合法的 seq")
    kind = record.get("kind")
    if kind == "entry":
        entry_id = record.get("id")
        parent_id = record.get("parentId")
        entry_type = record.get("type")
        timestamp = record.get("timestamp")
        if not isinstance(entry_id, str) or not entry_id:
            raise SessionStorageError("条目缺 id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise SessionStorageError("条目的 parentId 非法")
        if not isinstance(entry_type, str):
            raise SessionStorageError("条目缺 type")
        if not isinstance(timestamp, int) or timestamp < 0:
            raise SessionStorageError("条目缺合法的 timestamp")
        message = record.get("message")
        if message is not None and not isinstance(message, dict):
            raise SessionStorageError("条目的 message 必须是对象")
        return CommittedEntry(
            seq=seq,
            timestamp=timestamp,
            entry=NewEntry(
                id=entry_id, parent_id=parent_id, type=entry_type, message=message
            ),
        )
    if kind == "value":
        namespace = record.get("namespace")
        key = record.get("key")
        if not isinstance(namespace, str) or not namespace:
            raise SessionStorageError("值的 namespace 非法")
        if not isinstance(key, str):
            raise SessionStorageError("值的 key 非法")
        op = record.get("op")
        if op == "set":
            return CommittedValueSet(seq, namespace, key, record.get("value"))
        if op == "delete":
            return CommittedValueDelete(seq, namespace, key)
        raise SessionStorageError(f"值操作不认识：{op!r}")
    raise SessionStorageError(f"事务类型不认识：{kind!r}")


def encode_transaction(writes: Sequence[CommittedWrite]) -> str:
    """一行 = 一次提交。单写裸对象、多写数组，与 pi 的写法一致。"""
    records = [encode_write(write) for write in writes]
    payload: object = records[0] if len(records) == 1 else records
    return json.dumps(payload, ensure_ascii=False)


def parse_transaction(line: str) -> tuple[CommittedWrite, ...]:
    try:
        payload = json.loads(line)
    except ValueError as exc:
        raise SessionStorageError(f"事务不是合法 JSON：{exc}") from exc
    records = payload if isinstance(payload, list) else [payload]
    if not records:
        raise SessionStorageError("事务为空")
    return tuple(decode_write(record) for record in records)


def _split_complete_lines(content: str) -> tuple[list[str], bool]:
    """拆成完整行；末尾没有换行的那截视为撕裂残片。"""
    if content.endswith("\n"):
        return content[:-1].split("\n"), False
    last_newline = content.rfind("\n")
    if last_newline == -1:
        return [], True
    return content[:last_newline].split("\n"), True


def _fsync_write(path: Path, payload: str, *, append: bool) -> None:
    try:
        with path.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise SessionStorageError(f"会话写入失败：{path}（{exc}）") from exc


def _publish_atomically(path: Path, payload: str) -> None:
    """先写临时文件再 rename：读到的要么是旧内容，要么是新内容。"""
    temp_path = path.with_name(path.name + ".tmp")
    try:
        _fsync_write(temp_path, payload, append=False)
        os.replace(temp_path, path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise SessionStorageError(f"会话发布失败：{path}（{exc}）") from exc


class JsonlStorage:
    """一个会话文件。写入口只有 ``commit``，所以 I1（只追加）由它一个人守。"""

    def __init__(self, path: Path, header: JsonlHeader, *, now=None) -> None:
        self.path = path
        self.header = header
        self._now = now or now_ms
        self._state = SessionState()
        self._lock = threading.Lock()
        self._closed = False

    # ---------------- 构造 ----------------

    @classmethod
    def create(cls, path: Path, header: JsonlHeader, *, now=None) -> "JsonlStorage":
        path.parent.mkdir(parents=True, exist_ok=True)
        _publish_atomically(path, encode_header(header) + "\n")
        return cls(path, header, now=now)

    @classmethod
    def open(cls, path: Path, *, now=None) -> "JsonlStorage":
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc
        lines, torn = _split_complete_lines(content)
        if not lines or lines[0] == "":
            raise SessionStorageError(f"会话文件缺少 header：{path}")
        header = parse_header(lines[0])
        storage = cls(path, header, now=now)
        for index, line in enumerate(lines[1:], start=2):
            try:
                writes = parse_transaction(line)
                storage._state.validate(writes)
                storage._state.apply(writes)
            except Exception as exc:  # 解析、校验、应用失败都算这一行坏了
                raise SessionStorageError(f"{path} 第 {index} 行非法：{exc}") from exc
        if header.next_seq is not None:
            storage._state.advance_next_seq(header.next_seq)
        if torn:
            logger.warning("会话文件末尾有撕裂行，已重写：%s", path)
            _publish_atomically(path, "\n".join(lines) + "\n")
        return storage

    # ---------------- 数据面 ----------------

    def commit(self, writes: Sequence[Write]) -> CommitResult:
        self._assert_open()
        with self._lock:
            prepared = self._state.prepare_commit(writes, self._now())
            if prepared.writes:
                _fsync_write(self.path, encode_transaction(prepared.writes) + "\n", append=True)
            stats = self._state.apply(prepared.writes)
        return CommitResult(
            first_seq=prepared.first_seq,
            seqs=prepared.seqs,
            timestamp=prepared.timestamp,
            stats=stats,
        )

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]:
        self._assert_open()
        return self._state.get_entries(ids)

    def get_value(self, address: ValueAddress) -> StoredValue | None:
        self._assert_open()
        return self._state.get_value(address)

    def scan_values(self, namespace: str) -> list[StoredValue]:
        self._assert_open()
        return self._state.values_in(namespace)

    def scan_branch(self, query: BranchScan) -> list[Entry]:
        self._assert_open()
        return self._state.scan_branch(query)

    def scan_entries(self, query: EntryQuery) -> list[Entry]:
        self._assert_open()
        return self._state.scan_entries(query)

    def get_stats(self) -> SessionStats:
        self._assert_open()
        return self._state.stats

    def close(self) -> None:
        self._closed = True

    @property
    def next_seq(self) -> int:
        return self._state.next_seq

    def _assert_open(self) -> None:
        if self._closed:
            raise SessionClosedError(f"会话存储已关闭：{self.path}")


def session_file_name(created_at: int, session_id: str) -> str:
    stamp = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H-%M-%S"
    )
    return f"{stamp}-{created_at % 1000:03d}_{quote(session_id, safe='')}{SUFFIX}"


class JsonlSessionRepo:
    """``SessionRepo`` 的文件实现。列表只读 header，不把会话整个读进来。"""

    def __init__(
        self,
        root: str | Path,
        *,
        now=None,
        id_generator: IdGenerator | None = None,
        workspace: str | None = None,
    ) -> None:
        self.root = Path(root)
        # 本仓库服务哪个工作区。session/ 不推导工作区（它对 avid 内部零依赖），
        # 由调用方告诉它；老会话文件没有 workspaceId 时据此归属。
        self.workspace = workspace
        self._now = now or now_ms
        self._id_generator = id_generator or UuidV7Generator(self._now)
        self._open: dict[str, JsonlStorage] = {}
        self._pending: set[str] = set()
        self._closed = False

    # ---------------- 生命周期 ----------------

    def create(
        self,
        *,
        id: str | None = None,
        parent_session_id: str | None = None,
        workspace: str | None = None,
    ) -> StorageBackedSession:
        """``workspace`` 缺省时用仓库自己的归属（由会话库位置派生）。"""
        self._assert_open()
        session_id = validate_session_id(
            self._id_generator.next() if id is None else id
        )
        self._reserve(session_id)
        try:
            created_at = self._now()
            owner = self.workspace if workspace is None else workspace
            header = JsonlHeader(
                id=session_id,
                storage_version=STORAGE_VERSION,
                created_at=created_at,
                parent_session_id=parent_session_id,
                workspace=owner,
            )
            path = self.root / session_file_name(created_at, session_id)
            storage = JsonlStorage.create(path, header, now=self._now)
            metadata = JsonlSessionMetadata(
                id=session_id,
                created_at=created_at,
                storage_version=STORAGE_VERSION,
                parent_session_id=parent_session_id,
                path=path,
                workspace=owner,
            )
            return self._publish(metadata, storage)
        finally:
            self._pending.discard(session_id)

    def open(self, metadata: SessionMetadata) -> StorageBackedSession:
        self._assert_open()
        if metadata.id in self._open:
            raise SessionAlreadyOpenError(metadata.id)
        path = self._locate(metadata)
        storage = JsonlStorage.open(path, now=self._now)
        # 跨工作区护栏：_locate 优先用 metadata.path，而 open 只校验 header.id，
        # 于是把 A 工作区的 metadata 交给指向 B 的仓库会静默打开 A 的文件。
        if (
            self.workspace
            and storage.header.workspace
            and storage.header.workspace != self.workspace
        ):
            raise SessionStorageError(
                f"会话 {metadata.id} 属于另一个工作区（{storage.header.workspace}），"
                f"不能在 {self.workspace} 的仓库里打开"
            )
        if storage.header.id != metadata.id:
            raise SessionStorageError(
                f"文件的 id 与请求不符：{path} 里是 {storage.header.id}，请求的是 {metadata.id}"
            )
        if storage.header.storage_version != STORAGE_VERSION:
            raise SessionStorageError(
                f"会话 {metadata.id} 的存储版本不认识：{storage.header.storage_version}"
            )
        resolved = JsonlSessionMetadata(
            id=storage.header.id,
            created_at=storage.header.created_at,
            storage_version=storage.header.storage_version,
            parent_session_id=storage.header.parent_session_id,
            path=path,
            # 老文件没有 workspaceId：按仓库归属补上（位置即归属）。
            workspace=storage.header.workspace or self.workspace,
        )
        return self._publish(resolved, storage)

    def list(self) -> list[JsonlSessionMetadata]:
        self._assert_open()
        if not self.root.exists():
            return []
        found: list[JsonlSessionMetadata] = []
        for path in sorted(self.root.glob(f"*{SUFFIX}")):
            metadata = self._read_metadata(path)
            if metadata is not None:
                found.append(metadata)
        found.sort(key=lambda item: (-item.created_at, item.id))
        return found

    def delete(self, metadata: SessionMetadata) -> None:
        self._assert_open()
        if metadata.id in self._open:
            raise SessionAlreadyOpenError(metadata.id)
        path = self._locate(metadata)
        try:
            path.unlink()
        except OSError as exc:
            raise SessionStorageError(f"删除会话失败：{path}（{exc}）") from exc
        logger.info("会话已删除：%s", metadata.id)

    def close(self) -> None:
        self._closed = True
        for storage in list(self._open.values()):
            storage.close()
        self._open.clear()

    # ---------------- 内部 ----------------

    def _publish(
        self, metadata: JsonlSessionMetadata, storage: JsonlStorage
    ) -> StorageBackedSession:
        if metadata.id in self._open:
            raise SessionAlreadyOpenError(metadata.id)
        session = StorageBackedSession(
            metadata,
            storage,
            id_generator=self._id_generator,
            on_close=lambda: self._open.pop(metadata.id, None),
        )
        self._open[metadata.id] = storage
        return session

    def _locate(self, metadata: SessionMetadata) -> Path:
        path = getattr(metadata, "path", None)
        if isinstance(path, Path) and path.exists():
            return path
        for candidate in self._session_paths(metadata.id):
            return candidate
        raise SessionNotFoundError(metadata.id)

    def _session_paths(self, session_id: str) -> list[Path]:
        if not self.root.exists():
            return []
        suffix = f"_{quote(session_id, safe='')}{SUFFIX}"
        return [
            path
            for path in sorted(self.root.glob(f"*{SUFFIX}"))
            if path.name.endswith(suffix)
        ]

    def _reserve(self, session_id: str) -> None:
        if session_id in self._open or session_id in self._pending:
            raise SessionExistsError(session_id)
        if self._session_paths(session_id):
            raise SessionExistsError(session_id)
        self._pending.add(session_id)

    def _read_metadata(self, path: Path) -> JsonlSessionMetadata | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
            modified_at = int(path.stat().st_mtime * 1000)
        except OSError as exc:
            # 列表不该因为一个坏文件（读不了、刚好被删）整体失败。
            logger.warning("跳过读不了的会话文件 %s：%s", path, exc)
            return None
        if not first.endswith("\n"):
            logger.warning("跳过没有完整 header 的会话文件：%s", path)
            return None
        try:
            header = parse_header(first.rstrip("\n"))
        except SessionStorageError as exc:
            logger.warning("跳过 header 非法的会话文件 %s：%s", path, exc)
            return None
        return JsonlSessionMetadata(
            id=header.id,
            created_at=header.created_at,
            storage_version=header.storage_version,
            parent_session_id=header.parent_session_id,
            path=path,
            modified_at=modified_at,
            workspace=header.workspace or self.workspace,
        )

    def _assert_open(self) -> None:
        if self._closed:
            raise SessionClosedError("仓库已关闭，不能再创建或打开会话。")
