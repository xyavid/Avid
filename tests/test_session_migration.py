"""旧布局 → 集中会话目录的一次性迁移：归属认 header、目标已存在就跳过、在用的不硬搬。

会话文件用真的仓库写（`JsonlSessionRepo.create`），这样迁移面对的就是真实格式。
"""

from __future__ import annotations

from pathlib import Path

from avid.services.session_migration import apply_migration, plan_migration
from avid.session import JsonlSessionRepo
from avid.session.jsonl import SessionFileLock


def make_session(directory: Path, *, workspace: str | None, session_id: str | None = None):
    """在给定目录里用真仓库落一个会话，返回文件路径。"""
    repo = JsonlSessionRepo(directory, workspace=workspace)
    try:
        session = repo.create(id=session_id)
        return next(directory.glob(f"*_{session.metadata.id}.jsonl"))
    finally:
        repo.close()


def legacy_dir(root: Path) -> Path:
    return root / ".avid" / "sessions"


def test_plan_reads_the_owner_from_the_header(tmp_path):
    """header 的 workspaceId 是权威归属：文件躺在别人的旧目录里也回它自己的家。"""
    store = tmp_path / "store"
    source = legacy_dir(tmp_path / "project")
    file = make_session(source, workspace="w-other")

    plan = plan_migration(store=store, roots=[tmp_path / "project"])

    assert [(move.source, move.target, move.workspace_id) for move in plan.moves] == [
        (file, store / "w-other" / file.name, "w-other")
    ]
    assert plan.skips == ()


def test_plan_falls_back_to_the_root_id_when_the_header_has_none(tmp_path):
    """没有 workspaceId 的老文件按它所在的工作区根归属。"""
    from avid.services.workspace_registry import derive_id

    root = tmp_path / "project"
    store = tmp_path / "store"
    file = make_session(legacy_dir(root), workspace=None)

    plan = plan_migration(store=store, roots=[root])

    assert plan.moves[0].workspace_id == derive_id(root)
    assert plan.moves[0].target == store / derive_id(root) / file.name


def test_plan_skips_a_target_that_already_exists(tmp_path):
    root = tmp_path / "project"
    store = tmp_path / "store"
    file = make_session(legacy_dir(root), workspace="w-1")
    taken = store / "w-1" / file.name
    taken.parent.mkdir(parents=True)
    taken.write_text("已经有一个同名的了\n", encoding="utf-8")

    plan = plan_migration(store=store, roots=[root])

    assert plan.moves == ()
    assert [skip.source for skip in plan.skips] == [file]
    assert "已存在" in plan.skips[0].reason


def test_plan_skips_files_that_are_not_sessions(tmp_path):
    root = tmp_path / "project"
    source = legacy_dir(root)
    source.mkdir(parents=True)
    (source / "notes.jsonl").write_text("这不是本程序的文件\n", encoding="utf-8")

    plan = plan_migration(store=tmp_path / "store", roots=[root])

    assert plan.moves == ()
    assert "不是本程序的会话文件" in plan.skips[0].reason


def test_plan_ignores_roots_without_a_legacy_directory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    plan = plan_migration(store=tmp_path / "store", roots=[root])

    assert plan.moves == () and plan.skips == ()


def test_apply_moves_the_files_and_drops_the_empty_legacy_directory(tmp_path):
    root = tmp_path / "project"
    store = tmp_path / "store"
    file = make_session(legacy_dir(root), workspace="w-1")
    plan = plan_migration(store=store, roots=[root])

    tally = apply_migration(plan)

    assert [move.source for move in tally.moved] == [file]
    assert (store / "w-1" / file.name).exists()
    assert not file.exists()
    # 旧目录空了就删掉；.avid 留着（checkpoints / context 溢写还在那儿）。
    assert not legacy_dir(root).exists()
    assert (root / ".avid").exists()
    assert tally.removed_dirs == (legacy_dir(root),)


def test_apply_keeps_the_legacy_directory_when_something_else_lives_there(tmp_path):
    root = tmp_path / "project"
    file = make_session(legacy_dir(root), workspace="w-1")
    (legacy_dir(root) / "keep-me.txt").write_text("x", encoding="utf-8")

    tally = apply_migration(plan_migration(store=tmp_path / "store", roots=[root]))

    assert tally.removed_dirs == ()
    assert file.exists() is False
    assert (legacy_dir(root) / "keep-me.txt").exists()


def test_apply_skips_a_session_another_process_holds(tmp_path):
    """在用的会话不硬搬：它现在还在被写，搬走等于把那个进程的状态撕开。"""
    root = tmp_path / "project"
    file = make_session(legacy_dir(root), workspace="w-1")
    held = SessionFileLock(file)
    held.acquire("测试占住")
    try:
        tally = apply_migration(plan_migration(store=tmp_path / "store", roots=[root]))
    finally:
        held.release()

    assert tally.moved == ()
    assert "正在被另一个进程使用" in tally.skipped[0].reason
    assert file.exists()


def test_apply_rechecks_before_moving(tmp_path):
    """计划与执行之间别人建了同名会话：不覆盖，如实报跳过。"""
    root = tmp_path / "project"
    store = tmp_path / "store"
    file = make_session(legacy_dir(root), workspace="w-1")
    plan = plan_migration(store=store, roots=[root])
    taken = store / "w-1" / file.name
    taken.parent.mkdir(parents=True)
    taken.write_text("插队的\n", encoding="utf-8")

    tally = apply_migration(plan)

    assert tally.moved == ()
    assert tally.skipped[0].source == file
    assert taken.read_text(encoding="utf-8") == "插队的\n"
    assert file.exists()


def test_a_second_run_finds_nothing_left(tmp_path):
    root = tmp_path / "project"
    store = tmp_path / "store"
    make_session(legacy_dir(root), workspace="w-1")

    apply_migration(plan_migration(store=store, roots=[root]))

    assert plan_migration(store=store, roots=[root]).moves == ()


def test_from_dir_scans_a_store_root_by_its_workspace_subdirectories(tmp_path):
    """--from 指一个集中目录（下面按 id 分子目录）时逐个扫，id 取子目录名。"""
    old_store = tmp_path / "old-store"
    file = make_session(old_store / "w-9", workspace=None)

    plan = plan_migration(store=tmp_path / "new-store", from_dir=old_store)

    assert [(move.target, move.workspace_id) for move in plan.moves] == [
        (tmp_path / "new-store" / "w-9" / file.name, "w-9")
    ]


def test_from_dir_scans_a_flat_store(tmp_path):
    old = tmp_path / "flat"
    file = make_session(old, workspace="w-4")

    plan = plan_migration(store=tmp_path / "new-store", from_dir=old)

    assert [(move.target, move.workspace_id) for move in plan.moves] == [
        (tmp_path / "new-store" / "w-4" / file.name, "w-4")
    ]


def test_planning_from_the_store_itself_moves_nothing(tmp_path):
    """--from 指的就是当前会话目录：没有可搬的，也不该把文件搬进自己。"""
    store = tmp_path / "store"
    file = make_session(store / "w-1", workspace="w-1")

    plan = plan_migration(store=store, from_dir=store)

    assert plan.moves == ()
    assert plan.skips == ()
    assert file.exists()


def test_plan_deduplicates_overlapping_sources(tmp_path):
    """同一个旧目录既能从注册表推出来、又被 --from 指名：只算一次。"""
    root = tmp_path / "project"
    make_session(legacy_dir(root), workspace="w-1")

    plan = plan_migration(store=tmp_path / "store", roots=[root], from_dir=legacy_dir(root))

    assert len(plan.moves) == 1
