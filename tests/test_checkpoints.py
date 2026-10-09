"""agent/checkpoints: pre-write snapshots on disk and per-session-point restore."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from avid.agent.checkpoints import (
    DirCheckpointSink,
    RewindTarget,
    restore,
    restore_tally,
    rewind_target,
)
from avid.session.types import MESSAGE_ENTRY, NOTICE_ENTRY, Entry

SESSION = "s1"


class FixedTip:
    """A tip_seq probe stand-in returning a preset, advanceable seq."""

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
    """Roundtrip: snapshot before the write, restore after it, and the content is back."""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("旧内容\n", encoding="utf-8")

    assert sink.snapshot(target) is None
    target.write_text("新内容\n", encoding="utf-8")

    lines = restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert target.read_text(encoding="utf-8") == "旧内容\n"
    assert lines == [f"已恢复 {target}"]


def test_tombstone_restore_deletes_the_created_file(tmp_path):
    """Tombstone: the file did not exist at snapshot time, so restore deletes it."""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "created.txt"

    assert sink.snapshot(target) is None
    target.write_text("运行中新建", encoding="utf-8")

    lines = restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert not target.exists()
    assert lines == [f"已删除 {target}（该点时尚不存在）"]


def test_second_snapshot_in_the_same_seq_is_skipped(tmp_path):
    """Within one seq directory a path keeps only its earliest pre-write state."""
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
    """Paths under .avid are never snapshotted (recursion guard), leaving no checkpoint dir."""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / ".avid" / "checkpoints" / SESSION / "blob-0000"
    target.parent.mkdir(parents=True)
    target.write_text("自指", encoding="utf-8")

    assert sink.snapshot(target) is None
    assert not (session_dir(tmp_path) / "000000000001").exists()


def test_tip_seq_change_produces_a_new_seq_directory(tmp_path):
    """Each seq directory is named with 12 zero-padded digits."""
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
    """A probe error means no landing spot: the snapshot fails and the write must be refused."""

    def broken() -> int | None:
        raise RuntimeError("会话已关闭")

    sink = DirCheckpointSink(root=tmp_path, session_id=SESSION, tip_seq=broken)

    result = sink.snapshot(tmp_path / "a.txt")

    assert result is not None and "快照" in result
    assert not session_dir(tmp_path).exists()


def test_snapshot_without_a_session_tip_refuses(tmp_path):
    """A None tip means no committed entry to attach to; same failure and write refusal."""
    tip = FixedTip()
    tip.seq = None
    sink = DirCheckpointSink(root=tmp_path, session_id=SESSION, tip_seq=tip)

    result = sink.snapshot(tmp_path / "a.txt")

    assert result is not None and "快照" in result
    assert not session_dir(tmp_path).exists()


def test_blob_roundtrips_non_utf8_bytes(tmp_path):
    """Snapshots are byte-exact, so non-UTF-8 content restores unchanged."""
    sink, _ = make_sink(tmp_path)
    target = tmp_path / "raw.bin"
    target.write_bytes(b"\xff\xfe\x00binary")

    sink.snapshot(target)
    target.write_bytes(b"overwritten")

    restore(root=tmp_path, session_id=SESSION, through_seq=0)

    assert target.read_bytes() == b"\xff\xfe\x00binary"


def test_parallel_snapshots_of_the_same_seq_all_land(tmp_path):
    """Parallel subagents share one sink: concurrent snapshots of the same seq lose no entries."""
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
    """Restore takes the earliest pre-write content; through_seq advances that point."""
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


# ---------- rewind_target / restore_tally ----------


def chain_entry(
    seq: int, role: str, *, entry_type: str = MESSAGE_ENTRY, parent: str | None = None
) -> Entry:
    """Build a real entry on the branch chain; literal drift surfaces here."""
    return Entry(
        id=f"e{seq}",
        parent_id=parent,
        seq=seq,
        timestamp=0,
        type=entry_type,
        message={"role": role, "content": "x"},
    )


def test_rewind_target_finds_the_last_user_input():
    """The anchor is the last user transcript message: return its parent id and own seq."""
    chain = [
        chain_entry(1, "user"),
        chain_entry(2, "assistant", parent="e1"),
        chain_entry(3, "user", parent="e2"),
        chain_entry(4, "assistant", parent="e3"),
    ]

    assert rewind_target(chain) == RewindTarget(parent_id="e2", through_seq=3)


def test_rewind_target_hits_a_bare_user_tail():
    """A user message at the chain tail hits too; the first entry's parent is None."""
    assert rewind_target([chain_entry(1, "user")]) == RewindTarget(
        parent_id=None, through_seq=1
    )


def test_rewind_target_never_anchors_on_a_notice():
    """Notice entries share role user but never anchor, and never block an earlier anchor."""
    chain = [
        chain_entry(1, "user"),
        chain_entry(2, "assistant", parent="e1"),
        chain_entry(3, "user", entry_type=NOTICE_ENTRY, parent="e2"),
    ]

    assert rewind_target(chain) == RewindTarget(parent_id=None, through_seq=1)
    assert rewind_target([chain_entry(1, "user", entry_type=NOTICE_ENTRY)]) is None


def test_rewind_target_without_a_user_message_is_none():
    assert rewind_target([]) is None
    assert rewind_target(
        [chain_entry(1, "assistant"), chain_entry(2, "tool", parent="e1")]
    ) is None


def test_restore_tally_counts_only_successful_lines():
    lines = [
        "已恢复 /w/a.txt",
        "已删除 /w/b.txt（该点时尚不存在）",
        "恢复失败 /w/c.txt：boom",
        "没有需要恢复的文件",
    ]

    assert restore_tally(lines) == (1, 1)
    assert restore_tally([]) == (0, 0)
