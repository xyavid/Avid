"""Workspace registry: id derivation, idempotent registration, corruption tolerance.

The registry file is an index, never the authority.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avid.security import userdirs
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
    """Registration carries only a name; this version has no default-permission field."""
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
    """Read paths never write the file; only add and remove do."""
    registry.add(workspace_dir)
    before = registry.path.read_text(encoding="utf-8")

    registry.list()
    registry.find(workspace_dir)
    registry.get(str(workspace_dir))

    assert registry.path.read_text(encoding="utf-8") == before


def test_a_legacy_default_permission_field_is_ignored(registry, workspace_dir):
    """A legacy default_permission field is ignored on read and disappears on the next write."""
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
    """Removal is a tombstone: the entry leaves the candidate list, path and data stay."""
    marker = workspace_dir / "keep.txt"
    marker.write_text("会话数据", encoding="utf-8")
    registry.add(workspace_dir)

    removed = registry.remove(derive_id(workspace_dir))

    assert removed.id == derive_id(workspace_dir)
    assert removed.hidden is True
    assert registry.list() == []  # gone from the candidate list
    assert [ws.id for ws in registry.list(include_hidden=True)] == [removed.id]
    assert registry.find(str(workspace_dir)) is None  # find() misses it by default
    # the id is path-derived, so the entry must stay resolvable
    assert registry.get(derive_id(workspace_dir)).root == str(workspace_dir.resolve())
    assert marker.read_text(encoding="utf-8") == "会话数据"


def test_adding_a_hidden_workspace_again_brings_it_back(registry, workspace_dir):
    """Re-adding the same directory undoes the removal: same id, one entry, no second truth."""
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
    # permission is not persisted per workspace: each run passes it explicitly
    assert "default_permission" not in payload["workspaces"][0]
    assert payload["workspaces"][0]["root"] == str(workspace_dir.resolve())


def test_sessions_root_is_the_shared_store_keyed_by_workspace_id(registry, workspace_dir):
    """Sessions live in one dedicated store keyed by workspace id, not under ``<root>/.avid``."""
    workspace = registry.add(workspace_dir)

    assert sessions_root(workspace) == userdirs.sessions_dir() / workspace.id
    # an unregistered directory derives the same id from its path, so the landing spot matches
    assert sessions_root(str(workspace_dir)) == userdirs.sessions_dir() / workspace.id
    assert not Path(workspace.root, ".avid", "sessions").exists()


def test_sessions_root_follows_a_configured_store(tmp_path, monkeypatch, workspace_dir):
    monkeypatch.setenv(userdirs.SESSIONS_DIR_ENV, str(tmp_path / "on-another-disk"))

    assert sessions_root(workspace_dir) == tmp_path / "on-another-disk" / derive_id(workspace_dir)


def test_avid_home_overrides_the_user_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(AVID_HOME_ENV, str(tmp_path / "home"))

    assert registry_path() == tmp_path / "home" / "workspaces.json"
