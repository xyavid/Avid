"""agent/checkpoints 的单元测试：写前快照落盘与按会话点恢复。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from avid.agent.checkpoints import DirCheckpointSink, restore

SESSION = "s1"


class FixedTip:
    """tip_seq 探针替身：返回可推进的预设 seq。"""

    def __init__(self) -> None:
        self.seq: int | None = 1

    def __call__(self) -> int | None:
        return self.seq


def make_sink(root: Path) -> tuple[DirCheckpointSink, FixedTip]:
    tip = FixedTip()
    return DirCheckpointSink(root=root, session_id=SESSION, tip_seq=tip), tip


def session_dir(root: Path) -> Path:
    return root / ".avid" / "checkpoints" / SESSION


# ---------- snapshot ----------


def test_snapshot_then_restore_returns_the_pre_write_content(tmp_path):
    """roundtrip：改前快照，改完再 restore，内容回到快照时的样子。"""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("旧内容\n", encoding="utf-8")

    assert sink.snapshot(target) is None
    target.write_text("新内容\n", encoding="utf-8")

    lines = restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert target.read_text(encoding="utf-8") == "旧内容\n"
    assert lines == [f"已恢复 {target}"]


def test_tombstone_restore_deletes_the_created_file(tmp_path):
    """墓碑：快照时文件尚不存在，恢复 = 删除该文件。"""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "created.txt"

    assert sink.snapshot(target) is None
    target.write_text("运行中新建", encoding="utf-8")

    lines = restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert not target.exists()
    assert lines == [f"已删除 {target}（该点时尚不存在）"]


def test_second_snapshot_in_the_same_seq_is_skipped(tmp_path):
    """同一 seq 目录内同一路径只保留最早的「写前」状态。"""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("v0", encoding="utf-8")

    assert sink.snapshot(target) is None
    target.write_text("v1", encoding="utf-8")
    assert sink.snapshot(target) is None
    target.write_text("v2", encoding="utf-8")

    restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert target.read_text(encoding="utf-8") == "v0"


def test_paths_under_avid_are_never_snapshotted(tmp_path):
    """.avid 之下的路径不快照（防递归），也不留下任何检查点目录。"""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / ".avid" / "checkpoints" / SESSION / "blob-0000"
    target.parent.mkdir(parents=True)
    target.write_text("自指", encoding="utf-8")

    assert sink.snapshot(target) is None
    assert not (session_dir(tmp_path) / "000000000001").exists()


def test_tip_seq_change_produces_a_new_seq_directory(tmp_path):
    """落点跟随会话 tip 条目：seq 变化产生新的 12 位零填充分隔目录。"""
    sink, tip = make_sink(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("v0", encoding="utf-8")

    sink.snapshot(target)
    first = session_dir(tmp_path) / "000000000001"
    assert (first / "manifest.json").is_file()

    tip.seq = 2
    target.write_text("v1", encoding="utf-8")
    sink.snapshot(target)

    assert (session_dir(tmp_path) / "000000000002" / "manifest.json").is_file()
    entries = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert entries == [{"path": str(target), "blob": "blob-0000", "absent": False}]


def test_snapshot_fails_closed_when_the_probe_breaks(tmp_path):
    """tip_seq 探针异常 = 无法归属落点 = 快照失败，调用方必须拒写。"""

    def broken() -> int | None:
        raise RuntimeError("会话已关闭")

    sink = DirCheckpointSink(root=tmp_path, session_id=SESSION, tip_seq=broken)

    result = sink.snapshot(tmp_path / "a.txt")

    assert result is not None and "快照" in result
    assert not session_dir(tmp_path).exists()


def test_snapshot_without_a_session_tip_refuses(tmp_path):
    """tip 为 None = 会话还没有可归属的落库条目，同样按失败处理（拒写）。"""
    tip = FixedTip()
    tip.seq = None
    sink = DirCheckpointSink(root=tmp_path, session_id=SESSION, tip_seq=tip)

    result = sink.snapshot(tmp_path / "a.txt")

    assert result is not None and "快照" in result
    assert not session_dir(tmp_path).exists()


def test_blob_roundtrips_non_utf8_bytes(tmp_path):
    """快照按字节保存：非 UTF-8 内容也原样恢复。"""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "raw.bin"
    target.write_bytes(b"\xff\xfe\x00binary")

    sink.snapshot(target)
    target.write_bytes(b"overwritten")

    restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert target.read_bytes() == b"\xff\xfe\x00binary"


def test_parallel_snapshots_of_the_same_seq_all_land(tmp_path):
    """并行 subagent 共享一个 sink：同一 seq 的并发快照互不丢条目。"""
    sink, _ = make_sink(tmp_path)
    targets = [tmp_path / f"f{index}.txt" for index in range(8)]
    for target in targets:
        target.write_text("x", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(sink.snapshot, targets))

    assert results == [None] * 8
    entries = json.loads(
        (session_dir(tmp_path) / "000000000001" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert sorted(entry["path"] for entry in entries) == sorted(
        str(target) for target in targets
    )


# ---------- restore ----------


def test_earliest_snapshot_wins_across_seq_dirs(tmp_path):
    """多轮编辑：恢复取该路径第一次被改前的内容；through_seq 推进则取其后的最早一份。"""
    sink, tip = make_sink(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("v0", encoding="utf-8")

    sink.snapshot(target)
    target.write_text("v1", encoding="utf-8")
    tip.seq = 2
    sink.snapshot(target)
    target.write_text("v2", encoding="utf-8")

    restore(root=tmp_path, session_id=SESSION, through_seq=0)
    assert target.read_text(encoding="utf-8") == "v0"

    restore(root=tmp_path, session_id=SESSION, through_seq=1)
    assert target.read_text(encoding="utf-8") == "v1"


def test_restore_without_any_checkpoint_says_nothing_to_do(tmp_path):
    assert restore(root=tmp_path, session_id=SESSION, through_seq=0) == [
        "没有需要恢复的文件"
    ]


def test_restore_through_a_future_seq_says_nothing_to_do(tmp_path):
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("v0", encoding="utf-8")
    sink.snapshot(target)
    target.write_text("v1", encoding="utf-8")

    assert restore(root=tmp_path, session_id=SESSION, through_seq=5) == [
        "没有需要恢复的文件"
    ]
    assert target.read_text(encoding="utf-8") == "v1"
