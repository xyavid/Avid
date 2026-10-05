"""files 工具两个写路径（write_file / edit_file）的写前快照，与按会话点恢复。

覆盖边界：
- 只覆盖 files 工具的两个写路径；shell 明确不覆盖——shell 一条命令可写任意多个
  路径，写前无法可靠枚举目标，快照必漏，所以 shell 改过的文件不在可恢复承诺内。
- subagent 经 RunState.checkpoint 透传继承父会话的 sink；tip_seq 闭包指向父的
  recorder，父运行在本批等待期内一直有效。超时后仍在跑的子运行再快照时，探针
  会对已关闭的会话抛错，按下面的失败语义拒写。

失败语义：snapshot 返回字符串 = 备份失败，调用方必须拒绝写入——宁可拒写，不留
无快照的改动。探针返回 None（会话还没有任何落库条目，无从归属落点）同样算失败。

布局：<root>/.avid/checkpoints/<session_id>/<seq:012d>/，seq 是快照时会话分支
tip 条目的 seq；目录内 manifest.json 是条目数组 [{path, blob, absent}]，blob 是
同目录下的内容文件，absent=true 的墓碑表示该点时文件尚不存在（恢复 = 删除）。
同一 seq 目录内同一路径只保留第一份（最早的「写前」状态）；.avid 之下的路径
不快照（防递归）。

增长策略：不自动清理，快照随写随存；重审信号 = <root>/.avid/checkpoints 的目录
体积，膨胀到值得处理时再定保留策略。

一个 sink 实例服务一个会话的进程内生命周期：会话条目 seq 全局单调，每次落库
都会推进 tip，所以「当前 seq 已快照路径」的内存记录不会与其它 sink 实例冲突。
并行 subagent 共享同一个实例，snapshot 全程持锁。

/rewind 的纯件也在这里：rewind_target 在一条分支链上找回滚锚点，restore_tally
把 restore 的报告行折成计数。会话访问（tip 回指、游标清除）由调用方接线。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

_CHECKPOINTS_RELPATH = Path(".avid") / "checkpoints"
_MANIFEST = "manifest.json"
_SEQ_WIDTH = 12
_NOTHING_TO_RESTORE = "没有需要恢复的文件"

# 会话层条目判别（session.types.MESSAGE_ENTRY）：内核不反向 import 会话层，
# 这个字面量由用真实 Entry 构造的单元测试钉住。
_TRANSCRIPT_ENTRY = "message"


class DirCheckpointSink:
    """把写前快照落进 <root>/.avid/checkpoints/<session_id>/<seq>/，由会话接线方构造。"""

    def __init__(
        self, *, root: Path, session_id: str, tip_seq: Callable[[], int | None]
    ) -> None:
        self._root = Path(root)
        self._session_id = session_id
        self._tip_seq = tip_seq
        self._lock = threading.Lock()
        self._seq: int | None = None
        self._entries: list[dict[str, Any]] = []

    def snapshot(self, path: Path) -> str | None:
        """写前快照一个文件；None=成功，字符串=错误文案（调用方必须拒绝写入）。"""
        with self._lock:
            try:
                return self._snapshot_locked(path)
            except OSError as exc:
                return f"错误：写前快照失败：{exc}；已拒绝写入，文件保持原状"

    def _snapshot_locked(self, path: Path) -> str | None:
        if self._root / ".avid" in path.parents:
            return None  # 检查点目录自身的写入不快照，否则恢复会吞掉检查点
        try:
            seq = self._tip_seq()
        except Exception as exc:  # 会话已关闭等：落点无法归属，只能拒写
            return f"错误：写前快照失败：读取会话落点失败：{exc}；已拒绝写入"
        if seq is None:
            return "错误：写前快照失败：会话还没有可归属的落库条目；已拒绝写入"

        if seq != self._seq:
            self._seq = seq
            self._entries = []
        # seq 目录由 seq 派生而非保存：seq 单调，目录即当前落点。
        seq_dir = self._session_dir() / f"{seq:0{_SEQ_WIDTH}d}"
        seq_dir.mkdir(parents=True, exist_ok=True)

        key = str(path)
        if any(entry["path"] == key for entry in self._entries):
            return None  # 同一落点已保存最早的「写前」状态，再读只可能是改后的内容

        entry: dict[str, Any] = {"path": key, "blob": None, "absent": True}
        if path.exists():
            blob = f"blob-{len(self._entries):04d}"
            (seq_dir / blob).write_bytes(path.read_bytes())
            entry = {"path": key, "blob": blob, "absent": False}
        self._entries.append(entry)
        self._write_manifest(seq_dir)
        return None

    def _session_dir(self) -> Path:
        return self._root / _CHECKPOINTS_RELPATH / self._session_id

    def _write_manifest(self, seq_dir: Path) -> None:
        staging = seq_dir / (_MANIFEST + ".tmp")
        staging.write_text(
            json.dumps(self._entries, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(staging, seq_dir / _MANIFEST)  # manifest 任何时刻都可解析，恢复端才不必容错


def restore(*, root: Path, session_id: str, through_seq: int) -> list[str]:
    """把文件恢复到会话条目 seq=through_seq 时的样子，返回人读报告行。

    扫描 seq 大于 through_seq 的快照目录（升序），每个路径取最早一份——即该路径
    第一次被改之前的内容——写回字节，或按墓碑删除。没有备份目录或没有命中时返回
    单行「没有需要恢复的文件」；单个目录的 manifest 损坏按不存在跳过，单个路径
    恢复失败不挡住其余路径，都如实进报告。
    """
    session_dir = Path(root) / _CHECKPOINTS_RELPATH / session_id
    if not session_dir.is_dir():
        return [_NOTHING_TO_RESTORE]

    planned: dict[str, tuple[Path, dict[str, Any]]] = {}
    for seq_dir in _seq_dirs(session_dir, after=through_seq):
        try:
            entries = json.loads((seq_dir / _MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # 损坏的 manifest 不挡住其余目录的恢复
        for entry in entries:
            planned.setdefault(str(entry.get("path")), (seq_dir, entry))
    if not planned:
        return [_NOTHING_TO_RESTORE]

    lines: list[str] = []
    for key, (seq_dir, entry) in planned.items():
        try:
            if entry.get("absent"):
                Path(key).unlink(missing_ok=True)
                lines.append(f"已删除 {key}（该点时尚不存在）")
                continue
            blob = seq_dir / str(entry.get("blob"))
            target = Path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.read_bytes())
            lines.append(f"已恢复 {key}")
        except OSError as exc:
            lines.append(f"恢复失败 {key}：{exc}")
    return lines


def _seq_dirs(session_dir: Path, *, after: int) -> list[Path]:
    """seq 大于 after 的快照目录，升序；非数字命名的条目不是快照目录，跳过。"""
    found = []
    for child in session_dir.iterdir():
        if child.is_dir() and child.name.isdigit() and int(child.name) > after:
            found.append((int(child.name), child))
    return [path for _, path in sorted(found)]


class RewindTarget(NamedTuple):
    """一次 /rewind 的落点：对话指针回到 parent_id，文件恢复到 through_seq 时的样子。"""

    parent_id: str | None
    through_seq: int


class _ChainEntry(Protocol):
    """rewind_target 依赖的最小条目形状；会话层的 Entry 满足它（内核不 import 会话层）。"""

    type: str
    parent_id: str | None
    seq: int
    message: dict[str, Any] | None


def rewind_target(entries: Sequence[_ChainEntry]) -> RewindTarget | None:
    """在 oldest-first 的分支链上找最后一次用户输入，作为 /rewind 的锚点。

    只认 message 型且 role=user 的条目——notice 条目（role 同为 user 的内核注入）
    不是人说的话，不能当锚点。链上没有这样的条目返回 None（无可回滚）。锚点的
    seq 同时是 restore 的 through_seq：该输入落库时刻文件尚未被本轮改动。
    """
    for entry in reversed(entries):
        if entry.type != _TRANSCRIPT_ENTRY or entry.message is None:
            continue
        if entry.message.get("role") != "user":
            continue
        return RewindTarget(entry.parent_id, entry.seq)
    return None


def restore_tally(lines: Sequence[str]) -> tuple[int, int]:
    """把 restore 的报告行折成（恢复数，删除数）；失败行不算成功，如实落进文本。"""
    return (
        sum(1 for line in lines if line.startswith("已恢复")),
        sum(1 for line in lines if line.startswith("已删除")),
    )
