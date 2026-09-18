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
    SessionLockedError,
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
from .values import BRANCH_TIP_NS, DEFAULT_BRANCH, SESSION_NAME_NS, ValueAddress

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


# ---------------- 列表页摘要（不重放） ----------------

# 一个条目写恰好包含一次这个片段。消息正文里的同名字面量会被 JSON 转义成
# `\"kind\"`，因此不会误计——这条耦合由 `test_summarize_matches_replay` 用
# "正文里含该字面量"的用例钉住。
_ENTRY_MARKER = '"kind": "entry"'

# 判断"链尾是否残缺"只需要链尾附近的几条：判据在遇到第一个非 tool 条目时就返回，
# 所以正常情况下只需要链尾 + 它的几个工具结果。窗口大小直接决定解析代价（窗口里的
# 每一行都要 JSON 解析，而一条工具结果可能很大），因此取 32——判不出来时返回 None，
# 由调用方重放拿权威答案，不猜。
TAIL_WINDOW_LINES = 32


@dataclass(frozen=True)
class FileSummary:
    """列表页需要、且不必重放整个会话就能得到的三个事实。

    ``truncated_tail`` 为 ``None`` 表示"尾部窗口内判不出来"（例如刚在别的分支上
    追加了很多条目，默认分支的链尾落在窗口之外），调用方需要退回重放。
    """

    name: str | None
    message_count: int
    truncated_tail: bool | None


def _last_value(content: str, namespace: str) -> Any:
    """该 namespace 的最后一次写入（delete 也算一次，返回 None）。

    用 ``rfind`` 从后往前找候选行，再按行边界切开解析——不 splitlines 整个文件：
    值写入很少，候选行通常只有一两行，而整个文件可能有几千行。
    """
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
            # 残片/坏行：跳过，交给 open 的修复路径
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
    """文件末尾最多 window 行；不 splitlines 整个文件。

    ``rsplit`` 的 maxsplit=window 在"换行数 ≥ window"时会多出一个头部残段，
    按 len(parts) == window + 1 判断并丢掉它——否则窗口里会混进一大截文件头。
    """
    text = content[:-1] if content.endswith("\n") else content
    parts = text.rsplit("\n", window)
    return parts[1:] if len(parts) == window + 1 else parts


def _tail_state(lines: list[str]) -> tuple[dict[str, Any], bool, Any]:
    """尾部窗口内：条目表（id → NewEntry）、是否见过默认分支的链尾值、链尾值。"""
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
    """默认分支的链尾是否有一批没有结果的 tool_calls。

    与 ``svc.sessions._truncated_tail`` 同一判据（那一条是权威路径，走重放），
    差别只在这里只看尾部窗口：窗口不够就返回 None。
    """
    if not tip_seen:
        return False  # 默认分支还没有链尾值：没有可残缺的东西
    if not isinstance(tip, str) or tip not in entries:
        return None
    seen: set[str] = set()
    cursor: str | None = tip
    while cursor is not None:
        entry = entries.get(cursor)
        if entry is None:
            return None  # 链走到窗口之外
        message = entry.message or {}
        if message.get("role") == "tool":
            seen.add(str(message.get("tool_call_id")))
            cursor = entry.parent_id
            continue
        expected = {str(call.get("id")) for call in (message.get("tool_calls") or [])}
        return bool(expected - seen)
    return False


def summarize_file(path: Path, *, window: int = TAIL_WINDOW_LINES) -> FileSummary:
    """读一次文件，给出列表页要的三个事实，**不建 SessionState、不逐行重放**。

    代价从"解析每一行 + 建对象"降到"一次读 + 子串计数 + 解析尾部窗口"：列表页
    每次都要为每个会话付这笔钱，会话一多它就是首屏的主要成本。
    """
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
    """把目录项本身刷盘（rename 之后必须做）。Windows 打不开目录，直接跳过。"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - 取决于文件系统
        pass
    finally:
        os.close(fd)


def _rollback(fd: int, size: int) -> None:
    """尽力把文件截回写入前的大小；回滚失败也不能掩盖原始错误。"""
    try:
        os.ftruncate(fd, size)
    except OSError:  # pragma: no cover - 已无更好的补救
        logger.warning("回滚短写失败，文件可能残留半行", exc_info=True)


def _fsync_write(path: Path, payload: str, *, append: bool) -> None:
    """写一行并 fsync。

    用一次 ``os.write`` 而不是文本句柄的缓冲写：写入长度必须**校验**，短写
    （ENOSPC、信号打断）要回滚成"这一行没写过"，否则调用方以为提交成功、
    内存里的 seq 已经推进，磁盘上却少半行——下次打开就永久读不了这个文件。
    """
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
    """先写临时文件再 rename：读到的要么是旧内容，要么是新内容。"""
    temp_path = path.with_name(path.name + ".tmp")
    try:
        _fsync_write(temp_path, payload, append=False)
        os.replace(temp_path, path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise SessionStorageError(f"会话发布失败：{path}（{exc}）") from exc
    # rename 本身要落盘才算发布完成，否则掉电后目录项可能还指向旧文件。
    _fsync_dir(path.parent)


# ---------------- 跨进程互斥 ----------------

try:  # POSIX
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

except ImportError:  # pragma: no cover - Windows
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


class SessionFileLock:
    """一个会话文件的跨进程锁（旁挂 ``<会话文件>.lock``，不锁会话文件本身）。

    为什么不锁会话文件：撕裂行修复会 ``os.replace`` 换掉 inode，锁在旧 inode 上
    会随之失效。旁挂文件全程不动，锁的生命周期与句柄一致。

    锁文件**不删除**：删除会和"另一个进程刚打开同一路径"竞态（两个进程各持
    不同 inode 上的锁）。留下一个 0 字节的旁挂文件是更小的代价。
    """

    def __init__(self, session_path: Path) -> None:
        self.path = session_path.with_name(session_path.name + ".lock")
        self._fd: int | None = None

    def acquire(self, label: str) -> None:
        """拿到锁，或抛 ``SessionLockedError``。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise SessionStorageError(f"会话锁打不开：{self.path}（{exc}）") from exc
        if not _try_lock(fd):
            os.close(fd)
            raise SessionLockedError(label)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        except OSError:  # pragma: no cover
            pass
        finally:
            os.close(self._fd)
            self._fd = None


class JsonlStorage:
    """一个会话文件。写入口只有 ``commit``，所以 I1（只追加）由它一个人守。

    跨进程互斥由 ``SessionFileLock`` 在 ``create``/``open`` 时取得、``close`` 时
    释放——**读之前**就要拿到，否则"读到别人正在写的半行 → 判为撕裂 → 原子重写"
    会把对方刚提交的那一行覆盖掉。
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

    # ---------------- 构造 ----------------

    @classmethod
    def create(cls, path: Path, header: JsonlHeader, *, now=None) -> "JsonlStorage":
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
                except Exception as exc:  # 解析、校验、应用失败都算这一行坏了
                    if index == len(lines):
                        # **末行**坏了：它是崩溃/短写留下的残片（或并发写留下的重复
                        # seq），按残片丢掉并原子重写。丢掉中间某行则会静默丢历史，
                        # 那种情况必须报错让人看。
                        logger.warning("会话文件末行非法，按残片丢弃：%s（%s）", path, exc)
                        lines = lines[:-1]
                        repair_tail = True
                        break
                    raise SessionStorageError(f"{path} 第 {index} 行非法：{exc}") from exc
            if header.next_seq is not None:
                storage._state.advance_next_seq(header.next_seq)
            if repair_tail:
                logger.warning("会话文件末尾有残片（撕裂行或末行非法），已重写：%s", path)
                _publish_atomically(path, "\n".join(lines) + "\n")
            return storage
        except Exception:
            lock.release()
            raise

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
        # 列表页摘要：(mtime_ns, size) → FileSummary。列表在一次运行期间会被反复取，
        # 只有正在写的那一个会话会失效。
        self._summaries: dict[str, tuple[tuple[int, int], FileSummary]] = {}
        self._closed = False

    def summarize(self, metadata: SessionMetadata) -> FileSummary:
        """列表页的三个事实（名字 / 条数 / 链尾是否残缺），不打开句柄。

        不重放整个会话，也不与运行线程抢句柄：列表是只读视图，以前却要
        ``open()`` 一次（逐行重放 + 建对象 + 拿会话锁）。
        """
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
        try:
            stat = path.stat()
        except OSError:  # 刚被删/读不了：不缓存，让调用方看到真实错误
            return None
        return (stat.st_mtime_ns, stat.st_size)

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
        # 删除也是一条写路径：另一个进程正持有它时不能删（否则它下次提交会写进
        # 一个已经被 unlink 的 inode，或把别人刚要打开的文件抽走）。
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
