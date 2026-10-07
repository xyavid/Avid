"""工作区注册表：id 派生、幂等登记、损坏容错、以及"注册表只是索引"这条性质。"""

from __future__ import annotations

import json

import pytest

from avid.services.workspace_registry import (
    AVID_HOME_ENV,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspaceRegistry,
    WorkspaceRegistryCorrupt,
    derive_id,
    registry_path,
    sessions_root,
)


@pytest.fixture
def registry(tmp_path):
    return WorkspaceRegistry(tmp_path / "avid-home" / "workspaces.json")


@pytest.fixture
def workspace_dir(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


def test_id_is_derived_from_the_root(tmp_path):
    assert derive_id(tmp_path) == derive_id(str(tmp_path))
    assert derive_id(tmp_path) != derive_id(tmp_path / "other")
    assert derive_id(tmp_path).startswith("w-")


def test_add_then_find_by_id_and_path(registry, workspace_dir):
    created = registry.add(workspace_dir)

    assert created.id == derive_id(workspace_dir)
    assert created.root == str(workspace_dir.resolve())
    assert created.name == "project"
    assert registry.find(created.id) == created
    assert registry.find(str(workspace_dir)) == created
    assert registry.find(None) is None


def test_add_is_idempotent(registry, workspace_dir):
    first = registry.add(workspace_dir)
    second = registry.add(workspace_dir)

    assert first.id == second.id
    assert len(registry.list()) == 1


def test_add_updates_the_name(registry, workspace_dir):
    """登记只带名字：阶段 51 起工作区没有默认权限这种字段。"""
    registry.add(workspace_dir)
    renamed = registry.add(workspace_dir, name="重构")

    assert renamed.name == "重构"
    assert "default_permission" not in renamed.to_dict()
    assert len(registry.list()) == 1


def test_add_rejects_missing_and_non_directory(registry, tmp_path):
    with pytest.raises(WorkspaceError):
        registry.add(tmp_path / "nope")

    file = tmp_path / "a.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        registry.add(file)


def test_list_is_newest_used_first(registry, tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()

    stamps = iter([1000, 2000])
    registry = WorkspaceRegistry(registry.path, now=lambda: next(stamps))
    registry.add(old)
    registry.add(new)

    assert [ws.root for ws in registry.list()] == [
        str(new.resolve()),
        str(old.resolve()),
    ]


def test_registry_only_changes_on_explicit_writes(registry, workspace_dir):
    """读路径不写盘：列一次、找一个、打开都别生成或改动文件。

    启动与日常使用都不写盘是阶段 18 的收尾裁决——"看一眼注册表"与"起过服务"
    必须可区分。写入口只有 add / remove。
    """
    registry.add(workspace_dir)
    before = registry.path.read_text(encoding="utf-8")

    registry.list()
    registry.find(workspace_dir)
    registry.get(str(workspace_dir))

    assert registry.path.read_text(encoding="utf-8") == before


def test_a_legacy_default_permission_field_is_ignored(registry, workspace_dir):
    """旧注册表里的 default_permission 读时忽略、下次写入自然消失（阶段 51 没有默认权限）。"""
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": [
                    {
                        "id": derive_id(workspace_dir),
                        "root": str(workspace_dir.resolve()),
                        "name": "项目",
                        "created_at": 1,
                        "last_used_at": 1,
                        "default_permission": "manual",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    found = registry.find(str(workspace_dir))

    assert found is not None and found.name == "项目"
    assert "default_permission" not in found.to_dict()

    registry.add(workspace_dir, name="改名")

    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert payload["workspaces"][0]["name"] == "改名"
    assert "default_permission" not in payload["workspaces"][0]


def test_get_unknown_raises_with_a_usable_hint(registry):
    with pytest.raises(WorkspaceNotFound) as exc:
        registry.get("w-nope")

    assert "avid workspace add" in str(exc.value)


def test_remove_only_hides_the_index(registry, workspace_dir):
    """删除是**墓碑**：不在候选里了，路径与数据都还在。

    为什么不是把条目从文件里删掉：会话库在 ``<root>/.avid/sessions``，而工作区 id 是
    路径的派生值——条目一旦没了，"这个 id 对应哪个目录"就再没有记录，界面删掉工作区后
    它下面的会话连列举与打开都做不到。用户要的是"归到未归属的会话"，不是消失。
    """
    marker = workspace_dir / "keep.txt"
    marker.write_text("会话数据", encoding="utf-8")
    registry.add(workspace_dir)

    removed = registry.remove(derive_id(workspace_dir))

    assert removed.id == derive_id(workspace_dir)
    assert removed.hidden is True
    assert registry.list() == []  # 候选里没有了
    assert [ws.id for ws in registry.list(include_hidden=True)] == [removed.id]
    assert registry.find(str(workspace_dir)) is None  # 默认查不到（不会被当成"已登记"）
    assert registry.get(derive_id(workspace_dir)).root == str(workspace_dir.resolve())
    assert marker.read_text(encoding="utf-8") == "会话数据"


def test_adding_a_hidden_workspace_again_brings_it_back(registry, workspace_dir):
    """重新登记同一个目录 = 撤销删除（同一个 id、同一个条目，不是第二份真相）。"""
    registry.add(workspace_dir, name="项目")
    registry.remove(derive_id(workspace_dir))

    again = registry.add(workspace_dir)

    assert again.id == derive_id(workspace_dir)
    assert again.hidden is False
    assert [ws.id for ws in registry.list()] == [again.id]


def test_corrupt_registry_degrades_reads_but_refuses_writes(registry, workspace_dir):
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text("{ 这不是 JSON", encoding="utf-8")

    assert registry.list() == []
    assert registry.find("w-x") is None
    with pytest.raises(WorkspaceRegistryCorrupt) as exc:
        registry.add(workspace_dir)
    assert str(registry.path) in str(exc.value)


def test_registry_file_shape(registry, workspace_dir):
    registry.add(workspace_dir, name="项目")

    payload = json.loads(registry.path.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["workspaces"][0]["name"] == "项目"
    # 权限不再随工作区落盘：运行级 permission 只由每次运行的显式参数决定。
    assert "default_permission" not in payload["workspaces"][0]
    assert payload["workspaces"][0]["root"] == str(workspace_dir.resolve())


def test_sessions_root_is_inside_the_workspace(registry, workspace_dir):
    workspace = registry.add(workspace_dir)

    assert sessions_root(workspace) == workspace_dir.resolve() / ".avid/sessions"
    assert sessions_root(str(workspace_dir)) == (
        workspace_dir.resolve() / ".avid/sessions"
    )


def test_avid_home_overrides_the_user_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(AVID_HOME_ENV, str(tmp_path / "home"))

    assert registry_path() == tmp_path / "home" / "workspaces.json"
