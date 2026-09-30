"""File backend: a header line plus one JSON transaction per commit, fsynced before it returns."""

from __future__ import annotations

import builtins
import json
import logging
import os
import threading
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .errors import (
    SessionAlreadyOpenError,
    SessionClosedError,
    SessionExistsError,
    SessionLockedError,
    SessionNotFoundError,
    SessionStorageError,
)
from .ids import UuidV7Generator, now_ms, validate_session_id
from .session import StorageBackedSession
from .state import SessionState
from .types import (
    STORAGE_VERSION,
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
    StoredValue,
    Write,
)
from .values import BRANCH_TIP_NS, DEFAULT_BRANCH, SESSION_NAME_NS, ValueAddress

logger = logging.getLogger("avid.session.jsonl")

# Version of the transaction-line format; a header carrying any other value is rejected.
FORMAT_VERSION = 1
HEADER_KIND = "header"
# Session file suffix; listing and id lookup both key off it.
SUFFIX = ".jsonl"

# Codec: header and transaction lines to and from JSON.


@dataclass(frozen=True)
class JsonlHeader:
    """Header of a session file: identity, creation time, high-water seq, owning workspace."""

    id: str
    storage_version: int
    created_at: int
    parent_session_id: str | None = None
    # High-water seq stored in the header; it is only used when it is ahead of the replay.
    next_seq: int | None = None
    # Owning workspace, written once; files without it read back as None and inherit their location.
    workspace: str | None = None


def encode_header(header: JsonlHeader) -> str:
    # Wire keys are camelCase and optional fields are omitted entirely.
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
    # Every field is type-checked, so a corrupt header fails loudly instead of loading half of it.
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
    # A malformed or unknown record is an error; nothing is skipped silently.
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
    """One line per commit: a bare object for a single write and an array for several."""
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
    """Splits the file into complete lines and flags trailing bytes that form a torn fragment."""
    if content.endswith("\n"):
        return content[:-1].split("\n"), False
    last_newline = content.rfind("\n")
    if last_newline == -1:
        return [], True
    return content[:last_newline].split("\n"), True


# List-page summary: the facts that need no full replay.

# An entry write contains this fragment exactly once; the same text inside a message body is
# JSON-escaped, so counting occurrences stays accurate.
_ENTRY_MARKER = '"kind": "entry"'

# How many trailing lines a summary parses: the window bounds the cost, and an inconclusive
# verdict returns None instead of guessing.
TAIL_WINDOW_LINES = 32


@dataclass(frozen=True)
class FileSummary:
    """The three facts a list page needs without replaying; truncated_tail None means undecided."""

    name: str | None
    message_count: int
    truncated_tail: bool | None


def _last_value(content: str, namespace: str) -> Any:
    """Last write in a namespace, counting a delete as None, found by rfind over the file text."""
    marker = f'"{namespace}"'
    index = content.rfind(marker)
    while index != -1:
        line_start = content.rfind("\n", 0, index) + 1
        line_end = content.find("\n", index)
        if line_end == -1:
            line_end = len(content)
        try:
            writes = parse_transaction(content[line_start:line_end])
        except SessionStorageError:
            writes = ()
            # A torn or corrupt line is skipped here; open holds the repair path.
        for write in reversed(writes):
            if not isinstance(write, (CommittedValueSet, CommittedValueDelete)):
                continue
            if write.namespace != namespace:
                continue
            if isinstance(write, CommittedValueSet):
                return write.value
            return None
        index = content.rfind(marker, 0, line_start)
    return None


def _tail_lines(content: str, window: int) -> list[str]:
    """The last window lines only, dropping the extra leading fragment rsplit can yield."""
    text = content[:-1] if content.endswith("\n") else content
    parts = text.rsplit("\n", window)
    return parts[1:] if len(parts) == window + 1 else parts


def _tail_state(lines: list[str]) -> tuple[dict[str, Any], bool, Any]:
    """Over the tail window: the entry table, whether the default tip was seen, and that tip."""
    entries: dict[str, Any] = {}
    tip_seen = False
    tip: Any = None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            writes = parse_transaction(line)
        except SessionStorageError:
            continue
        for write in reversed(writes):
            if isinstance(write, CommittedEntry):
                entries.setdefault(write.entry.id, write.entry)
            elif (
                isinstance(write, CommittedValueSet)
                and write.namespace == BRANCH_TIP_NS
                and write.key == DEFAULT_BRANCH
                and not tip_seen
            ):
                tip_seen = True
                tip = write.value
            elif (
                isinstance(write, CommittedValueDelete)
                and write.namespace == BRANCH_TIP_NS
                and write.key == DEFAULT_BRANCH
                and not tip_seen
            ):
                tip_seen = True
                tip = None
    return entries, tip_seen, tip


def _tail_is_truncated(entries: dict[str, Any], tip_seen: bool, tip: Any) -> bool | None:
    """Whether the default branch tip still has tool calls without results; None if undecidable."""
    if not tip_seen:
        return False  # no tip value yet, so nothing can be truncated
    if not isinstance(tip, str) or tip not in entries:
        return None
    seen: set[str] = set()
    cursor: str | None = tip
    while cursor is not None:
        entry = entries.get(cursor)
        if entry is None:
            return None  # the chain leaves the window, so the answer needs a replay
        message = entry.message or {}
        if message.get("role") == "tool":
            seen.add(str(message.get("tool_call_id")))
            cursor = entry.parent_id
            continue
        expected = {str(call.get("id")) for call in (message.get("tool_calls") or [])}
        return bool(expected - seen)
    return False


def summarize_file(path: Path, *, window: int = TAIL_WINDOW_LINES) -> FileSummary:
    """Reads the file once for the three list facts, building no state and replaying no line."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc
    name = _last_value(content, SESSION_NAME_NS)
    entries, tip_seen, tip = _tail_state(_tail_lines(content, window))
    return FileSummary(
        name=name if isinstance(name, str) else None,
        message_count=content.count(_ENTRY_MARKER),
        truncated_tail=_tail_is_truncated(entries, tip_seen, tip),
    )


def _fsync_dir(path: Path) -> None:
    """Flushes the directory entry after a rename; Windows cannot open a directory, so skipped."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - depends on the filesystem
        pass
    finally:
        os.close(fd)


def _rollback(fd: int, size: int) -> None:
    """Best-effort truncate back to the pre-write size; a failure must not hide the real error."""
    try:
        os.ftruncate(fd, size)
    except OSError:  # pragma: no cover - nothing better is available
        logger.warning("回滚短写失败，文件可能残留半行", exc_info=True)


def _fsync_write(path: Path, payload: str, *, append: bool) -> None:
    """Writes one line and fsyncs it, checking the byte count so a short write can roll back."""
    data = payload.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        raise SessionStorageError(f"会话写入失败：{path}（{exc}）") from exc
    try:
        original = os.fstat(fd).st_size
        try:
            written = os.write(fd, data)
        except OSError as exc:
            _rollback(fd, original)
            raise SessionStorageError(f"会话写入失败：{path}（{exc}）") from exc
        if written != len(data):
            _rollback(fd, original)
            raise SessionStorageError(
                f"会话写入不完整：{path}（写了 {written}/{len(data)} 字节，已回滚该行）"
            )
        try:
            os.fsync(fd)
        except OSError as exc:
            raise SessionStorageError(f"会话刷盘失败：{path}（{exc}）") from exc
    finally:
        os.close(fd)


def _publish_atomically(path: Path, payload: str) -> None:
    """Writes a temporary file and renames it, so a reader sees old or new content, never both."""
    temp_path = path.with_name(path.name + ".tmp")
    try:
        _fsync_write(temp_path, payload, append=False)
        os.replace(temp_path, path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise SessionStorageError(f"会话发布失败：{path}（{exc}）") from exc
    # The rename must reach disk as well, or a power loss can leave the old directory entry.
    _fsync_dir(path.parent)


# Cross-process lock: POSIX flock here, msvcrt byte-range locking on Windows.

try:  # POSIX: flock on the open lock fd
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

except ImportError:  # pragma: no cover - Windows: one locked byte stands in for flock
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        with suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]


class SessionFileLock:
    """Cross-process lock for one session file, held in a sidecar lock file.

    A sidecar is used because a torn-tail repair replaces the session file's inode.
    """

    def __init__(self, session_path: Path) -> None:
        # The sidecar stays behind on purpose: deleting it would race with another opener.
        self.path = session_path.with_name(session_path.name + ".lock")
        self._fd: int | None = None
        # A run thread and a repository close can release at once, so the fd needs its own lock.
        self._mutex = threading.Lock()

    def acquire(self, label: str) -> None:
        """Takes the lock or raises SessionLockedError with the given label."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise SessionStorageError(f"会话锁打不开：{self.path}（{exc}）") from exc
        if not _try_lock(fd):
            os.close(fd)
            raise SessionLockedError(label)
        with self._mutex:
            self._fd = fd

    def release(self) -> None:
        with self._mutex:
            fd, self._fd = self._fd, None
        if fd is None:
            return
        with suppress(OSError):  # pragma: no cover - unlocking is unsupported on some platforms
            _unlock(fd)
        os.close(fd)


class JsonlStorage:
    """One session file; commit is the only write path, under a lock held from open to close.

    Holding it that long keeps a torn-tail rewrite from clobbering a commit made meanwhile.
    """

    def __init__(
        self,
        path: Path,
        header: JsonlHeader,
        *,
        now=None,
        file_lock: SessionFileLock | None = None,
    ) -> None:
        self.path = path
        self.header = header
        self._now = now or now_ms
        self._state = SessionState()
        self._lock = threading.Lock()
        self._file_lock = file_lock
        self._closed = False

    # Construction: create a fresh file, or replay an existing one, both under the file lock.

    @classmethod
    def create(cls, path: Path, header: JsonlHeader, *, now=None) -> "JsonlStorage":
        """Creates a new session file under the lock, publishing its header atomically."""
        lock = SessionFileLock(path)
        lock.acquire(header.id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _publish_atomically(path, encode_header(header) + "\n")
        except Exception:
            lock.release()
            raise
        return cls(path, header, now=now, file_lock=lock)

    @classmethod
    def open(cls, path: Path, *, now=None) -> "JsonlStorage":
        """Replays the file under the lock, dropping a torn or invalid last line and rewriting."""
        lock = SessionFileLock(path)
        lock.acquire(path.name)
        try:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc
            lines, torn = _split_complete_lines(content)
            if not lines or lines[0] == "":
                raise SessionStorageError(f"会话文件缺少 header：{path}")
            header = parse_header(lines[0])
            storage = cls(path, header, now=now, file_lock=lock)
            repair_tail = torn
            for index, line in enumerate(lines[1:], start=2):
                try:
                    writes = parse_transaction(line)
                    storage._state.validate(writes)
                    storage._state.apply(writes)
                except Exception as exc:  # a parse, validation or apply failure all mean a bad line
                    if index == len(lines):
                        # A bad last line is a crash or short-write fragment: drop it and rewrite.
                        # A bad middle line would silently drop history, so it must raise instead.
                        logger.warning("会话文件末行非法，按残片丢弃：%s（%s）", path, exc)
                        lines = lines[:-1]
                        repair_tail = True
                        break
                    raise SessionStorageError(f"{path} 第 {index} 行非法：{exc}") from exc
            if header.next_seq is not None:
                # The header high-water mark matters only when it is ahead of the replayed seq.
                storage._state.advance_next_seq(header.next_seq)
            if repair_tail:
                logger.warning("会话文件末尾有残片（撕裂行或末行非法），已重写：%s", path)
                _publish_atomically(path, "\n".join(lines) + "\n")
            return storage
        except Exception:
            lock.release()
            raise

    # Data plane: reads go to the state, and commit is the only writer.

    def commit(self, writes: Sequence[Write]) -> CommitResult:
        """Allocates seq and timestamp, appends one fsynced line, then applies them to state."""
        self._assert_open()
        with self._lock:
            try:
                prepared = self._state.prepare_commit(writes, self._now())
                if prepared.writes:
                    _fsync_write(
                        self.path,
                        encode_transaction(prepared.writes) + "\n",
                        append=True,
                    )
            except (TypeError, ValueError) as exc:
                # Only SessionError escapes upward: values that json cannot serialise and text that
                # UTF-8 cannot encode are folded into it here.
                raise SessionStorageError(
                    f"这一条无法写入会话（{type(exc).__name__}：{exc}）"
                ) from exc
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
        if self._file_lock is not None:
            self._file_lock.release()
            self._file_lock = None

    @property
    def next_seq(self) -> int:
        return self._state.next_seq

    def _assert_open(self) -> None:
        if self._closed:
            raise SessionClosedError(f"会话存储已关闭：{self.path}")


def session_file_name(created_at: int, session_id: str) -> str:
    """File name: a UTC timestamp with milliseconds plus the quoted id, so names sort by time."""
    stamp = datetime.fromtimestamp(created_at / 1000, tz=UTC).strftime(
        "%Y-%m-%dT%H-%M-%S"
    )
    return f"{stamp}-{created_at % 1000:03d}_{quote(session_id, safe='')}{SUFFIX}"


class JsonlSessionRepo:
    """File implementation of SessionRepo; listing reads headers instead of replaying sessions."""

    def __init__(
        self,
        root: str | Path,
        *,
        now=None,
        id_generator: IdGenerator | None = None,
        workspace: str | None = None,
    ) -> None:
        self.root = Path(root)
        # Which workspace this repository serves; the caller passes it, and files without a
        # workspace entry inherit it from their location.
        self.workspace = workspace
        self._now = now or now_ms
        self._id_generator = id_generator or UuidV7Generator(self._now)
        self._open: dict[str, JsonlStorage] = {}
        self._pending: set[str] = set()
        # List summaries keyed by id, with (mtime_ns, size) as the validity stamp.
        self._summaries: dict[str, tuple[tuple[int, int], FileSummary]] = {}
        self._closed = False

    def summarize(self, metadata: SessionMetadata) -> FileSummary:
        """The three list facts for one session, without opening a handle or replaying it."""
        self._assert_open()
        path = self._locate(metadata)
        stamp = self._stamp(path)
        if stamp is not None:
            cached = self._summaries.get(metadata.id)
            if cached is not None and cached[0] == stamp:
                return cached[1]
        summary = summarize_file(path)
        if stamp is not None:
            self._summaries[metadata.id] = (stamp, summary)
        return summary

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int] | None:
        """Stat-based cache key; None (unreadable file) disables caching for this call."""
        try:
            stat = path.stat()
        except OSError:  # just deleted or unreadable: do not cache, surface the real error
            return None
        return (stat.st_mtime_ns, stat.st_size)

    # Lifecycle: create, open, list and delete.

    def create(
        self,
        *,
        id: str | None = None,
        parent_session_id: str | None = None,
        workspace: str | None = None,
    ) -> StorageBackedSession:
        """Creates a session, defaulting the owning workspace to the repository's own."""
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
        # metadata.path alone would let a repository open a file that belongs to another workspace.
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
            # A file without a workspace entry belongs to the repository that found it.
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
        """Deletes the file under the same id and workspace guards as open, holding the lock."""
        self._assert_open()
        if metadata.id in self._open:
            raise SessionAlreadyOpenError(metadata.id)
        path = self._locate(metadata)
        # Same guard as open: metadata.path alone must not be enough to delete someone else's file.
        header = self._header_of(path)
        if header.id != metadata.id:
            raise SessionStorageError(
                f"文件的 id 与请求不符：{path} 里是 {header.id}，请求的是 {metadata.id}"
            )
        if (
            self.workspace
            and header.workspace
            and header.workspace != self.workspace
        ):
            raise SessionStorageError(
                f"会话 {metadata.id} 属于另一个工作区（{header.workspace}），"
                f"不能在 {self.workspace} 的仓库里删除"
            )
        # Deleting is a write path: a concurrent holder must not lose its session to an unlink.
        lock = SessionFileLock(path)
        lock.acquire(metadata.id)
        try:
            try:
                path.unlink()
            except OSError as exc:
                raise SessionStorageError(f"删除会话失败：{path}（{exc}）") from exc
        finally:
            lock.release()
        logger.info("会话已删除：%s", metadata.id)
        self._summaries.pop(metadata.id, None)

    def close(self) -> None:
        self._closed = True
        for storage in list(self._open.values()):
            storage.close()
        self._open.clear()
        self._summaries.clear()

    # Internals: handle lookup, metadata reads and id reservation.

    def _publish(
        self, metadata: JsonlSessionMetadata, storage: JsonlStorage
    ) -> StorageBackedSession:
        if metadata.id in self._open:
            raise SessionAlreadyOpenError(metadata.id)
        def forget() -> None:
            self._open.pop(metadata.id, None)

        # on_close unregisters the id, so the same session can be opened again later.
        session = StorageBackedSession(
            metadata,
            storage,
            id_generator=self._id_generator,
            on_close=forget,
        )
        self._open[metadata.id] = storage
        return session

    def _locate(self, metadata: SessionMetadata) -> Path:
        path = getattr(metadata, "path", None)
        if isinstance(path, Path) and path.exists():
            return path
        # A stale recorded path falls back to the first filename match for the id.
        for candidate in self._session_paths(metadata.id):
            return candidate
        raise SessionNotFoundError(metadata.id)

    def _session_paths(self, session_id: str) -> builtins.list[Path]:
        """Files for one id: the id follows the first underscore, so matching splits on it."""
        if not self.root.exists():
            return []
        want = f"{quote(session_id, safe='')}{SUFFIX}"
        found: builtins.list[Path] = []
        for path in sorted(self.root.glob(f"*_{want}")):
            _, _, tail = path.name.partition("_")
            if tail == want:
                found.append(path)
        return found

    def _reserve(self, session_id: str) -> None:
        # Reserve the id before the file exists, so two concurrent creates cannot both win.
        if session_id in self._open or session_id in self._pending:
            raise SessionExistsError(session_id)
        if self._session_paths(session_id):
            raise SessionExistsError(session_id)
        self._pending.add(session_id)

    @staticmethod
    def _header_of(path: Path) -> JsonlHeader:
        """Parses the first line only; listing and deletion share it so they cannot diverge."""
        try:
            with path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
        except OSError as exc:
            raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc
        if not first.endswith("\n"):
            raise SessionStorageError(f"会话文件缺少完整 header：{path}")
        return parse_header(first.rstrip("\n"))

    def _read_metadata(self, path: Path) -> JsonlSessionMetadata | None:
        try:
            modified_at = int(path.stat().st_mtime * 1000)
            header = self._header_of(path)
        except OSError as exc:
            # One unreadable file must not fail the whole listing.
            logger.warning("跳过读不了的会话文件 %s：%s", path, exc)
            return None
        except SessionStorageError as exc:
            # Same for a file whose header is invalid.
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
