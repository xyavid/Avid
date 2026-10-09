"""The workspace HTTP surface: candidates, registration, mandatory ownership on create,
and the run-level full-access credential.

Creating a session without a workspace is 400 (ownership is an immutable fact of the session);
sessions land under the shared store's <workspace id>/ with workspaceId in the header; permission
has one run-level value: normal by default, full_access_ack for full access.
"""

from __future__ import annotations

import pytest
from support import ScriptedChat, make_turn

from avid.services import Services
from avid.services.workspace_registry import WorkspaceRegistry, sessions_root
from avid.web import create_app


@pytest.fixture
def multi(tmp_path):
    """A workspace outside the repo plus an empty registry: workspace_root must point at a temp
    dir, or Services binds the repo as the default root and the test writes sessions into it."""
    home = tmp_path.parent / f"bound-{tmp_path.name}"
    home.mkdir()
    registry = WorkspaceRegistry()
    services = Services(
        workspace_root=home, registry=registry, chat=ScriptedChat([make_turn("答")])
    )
    yield services, registry
    services.close()


@pytest.fixture
def client(multi, sandbox):
    from fastapi.testclient import TestClient

    services, registry = multi
    return TestClient(
        create_app(services=services, static_dir=sandbox / "unbuilt"),
        base_url="http://127.0.0.1:8765",
    )


def test_direct_sessions_root_still_binds_one_workplace(tmp_path):
    """An explicit store root still binds one workspace: the process always binds one place,
    and it resolves (so it is listed and flagged is_default).
    """
    from fastapi.testclient import TestClient

    from avid.services import Services
    from avid.web import create_app

    root = tmp_path.parent / f"direct-{tmp_path.name}" / ".avid" / "sessions"
    root.mkdir(parents=True)
    # Its own registry file so the other Services in this test does not leak in
    services = Services(root=root, registry=WorkspaceRegistry(tmp_path / "registry.json"))
    try:
        single = TestClient(
            create_app(services=services, static_dir="/tmp/unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        listed = single.get("/api/workspaces").json()["workspaces"]

        assert len(listed) == 1
        assert listed[0]["is_default"] is True
        assert listed[0]["id"].startswith("w-")
        # Ownership resolves: creating with it is 201 and the session lands in the given store
        created = single.post("/api/sessions", json={"workspace": listed[0]["id"]})
        assert created.status_code == 201, created.text
        assert list(root.glob("*.jsonl"))
    finally:
        services.close()


def test_create_session_always_names_a_workspace(client):
    """A session must always name a workspace (the bound value is only a suggestion), since
    omitting it would make immutable ownership depend on server state."""
    listed = client.get("/api/workspaces").json()["workspaces"]
    assert any(ws["is_default"] for ws in listed)

    response = client.post("/api/sessions", json={"name": "没有归属"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "workspace_required"

    bound = next(ws for ws in listed if ws["is_default"])
    explicit = client.post("/api/sessions", json={"workspace": bound["id"]})
    assert explicit.status_code == 201, explicit.text


def test_create_session_rejects_an_unknown_field(client):
    """Unknown fields are 422 rather than silently defaulted (extra=forbid)."""
    response = client.post("/api/sessions", json={"workspace": "w-x", "permissions": "system"})

    assert response.status_code == 422


def test_register_then_create_a_session_in_that_workspace(client, sandbox, tmp_path):
    project = tmp_path.parent / f"proj-{tmp_path.name}"
    project.mkdir()

    created = client.post("/api/workspaces", json={"path": str(project), "name": "项目"})
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert workspace["name"] == "项目"
    # No default permission: sending it in the payload is 422 and the echo has no such key
    assert "default_permission" not in workspace

    listed = client.get("/api/workspaces").json()["workspaces"]
    # The bound workspace is a candidate too, so assert containment, not equality
    assert workspace["id"] in {item["id"] for item in listed}

    session = client.post("/api/sessions", json={"workspace": workspace["id"]})
    assert session.status_code == 201, session.text
    detail = session.json()
    assert detail["workspace"]["id"] == workspace["id"]
    assert detail["workspace"]["root"] == workspace["root"]

    # Ownership persists: the file lands under the store's <workspace id>/, header carries it
    files = list(sessions_root(project).glob("*.jsonl"))
    assert len(files) == 1
    assert f'"workspaceId": "{workspace["id"]}"' in files[0].read_text(encoding="utf-8")

    # The list carries ownership too, and the session is reachable by id
    summary = client.get("/api/sessions").json()["sessions"][0]
    assert summary["workspace"]["id"] == workspace["id"]
    assert client.get(f"/api/sessions/{detail['id']}").json()["workspace"]["id"] == workspace["id"]


def test_delete_a_workspace_keeps_its_sessions_listed(client, tmp_path):
    """Deleting a workspace only removes it from the candidates: its sessions stay listed and
    openable, or deletion would silently destroy them.
    """
    project = tmp_path.parent / f"del-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    removed = client.delete(f"/api/workspaces/{ws['id']}")

    assert removed.status_code == 204, removed.text
    listed_workspaces = client.get("/api/workspaces").json()["workspaces"]
    assert ws["id"] not in {item["id"] for item in listed_workspaces}

    listed = client.get("/api/sessions").json()["sessions"]
    assert [item["id"] for item in listed] == [session["id"]]
    # Ownership keeps the id; the UI groups it as unowned because it is not a candidate
    assert listed[0]["workspace"]["id"] == ws["id"]
    assert client.get(f"/api/sessions/{session['id']}").status_code == 200
    assert sessions_root(project).exists()


def test_delete_the_bound_workspace_is_refused(client):
    """The bound workspace never enters the registry, so deleting it is refused (409)."""
    bound = next(
        item for item in client.get("/api/workspaces").json()["workspaces"] if item["is_default"]
    )

    response = client.delete(f"/api/workspaces/{bound['id']}")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "workspace_bound"


def test_delete_an_unknown_workspace_is_404(client):
    response = client.delete("/api/workspaces/w-nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workspace_not_found"


def test_a_deleted_workspace_can_be_registered_again(client, tmp_path):
    """Re-adding the same directory reuses the same id (the tombstone is reused, not doubled)."""
    project = tmp_path.parent / f"readd-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    client.delete(f"/api/workspaces/{ws['id']}")

    again = client.post("/api/workspaces", json={"path": str(project)})

    assert again.status_code == 201, again.text
    assert again.json()["id"] == ws["id"]


def test_meta_declares_workspace_delete(client):
    """The capability table gates the UI delete button: without it the button must not be drawn."""
    assert client.get("/api/meta").json()["features"]["workspace_delete"] == 1


def test_register_rejects_a_missing_directory(client):
    response = client.post("/api/workspaces", json={"path": "/tmp/avid-nope-nope"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "workspace_invalid"


def test_sessions_from_two_workspaces_are_listed_together(client, tmp_path):
    first = tmp_path.parent / f"a-{tmp_path.name}"
    second = tmp_path.parent / f"b-{tmp_path.name}"
    first.mkdir()
    second.mkdir()
    ids = []
    for path in (first, second):
        ws = client.post("/api/workspaces", json={"path": str(path)}).json()
        ids.append(ws["id"])
        client.post("/api/sessions", json={"workspace": ws["id"]})

    listed = client.get("/api/sessions").json()["sessions"]

    assert {item["workspace"]["id"] for item in listed} == set(ids)
    assert len(listed) == 2


def test_run_records_the_workspace_and_full_permission(client, tmp_path, sandbox):
    project = tmp_path.parent / f"run-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    # full_access_ack grants full access (sent only after the front end's two-step confirm)
    started = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": "问题", "auto_approve": True, "full_access_ack": True},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    # run_started carries ownership and permission: a page refresh rebuilds the UI from it
    stream = client.get(f"/api/runs/{run_id}/events").text
    assert '"workspace"' in stream
    assert ws["id"] in stream
    assert '"permission": "full"' in stream


def test_run_permission_defaults_to_normal(client, tmp_path, sandbox):
    project = tmp_path.parent / f"default-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    started = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": "问题", "auto_approve": True},
    )
    run_id = started.json()["run_id"]

    # No workspace default to inherit: without the ack the run is normal
    stream = client.get(f"/api/runs/{run_id}/events").text
    assert '"permission": "normal"' in stream


def test_run_rejects_a_permission_field(client, tmp_path):
    """The permission field no longer exists: any permission in the request is 422."""
    project = tmp_path.parent / f"bad-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    for value in ("yolo", "normal", "auto"):
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "问题", "permission": value},
        )
        assert response.status_code == 422, response.text


def test_startup_writes_nothing_to_the_registry(tmp_path):
    """Startup writes nothing to the registry (only an explicit user action does), though the
    bound value still appears as a candidate and resolves without being registered."""
    home = tmp_path.parent / f"quiet-{tmp_path.name}"
    home.mkdir()
    registry_file = tmp_path / "registry.json"
    registry = WorkspaceRegistry(registry_file)
    services = Services(workspace_root=home, registry=registry)
    try:
        listed = services.workspaces.list()

        assert not registry_file.exists()
        assert [ws["id"] for ws in listed] == [services.workspaces.default.id]
        assert listed[0]["is_default"] is True

        # Unregistered but resolvable: naming its id creates a session in its subdirectory
        created = services.sessions.create(workspace=listed[0]["id"])
        assert created["workspace"]["id"] == listed[0]["id"]
        assert list(sessions_root(home).glob("*.jsonl"))
        assert not registry_file.exists()  # creating a session does not write it either
    finally:
        services.close()


def test_explicit_registration_is_the_only_writer(tmp_path):
    home = tmp_path.parent / f"explicit-{tmp_path.name}"
    home.mkdir()
    other = tmp_path.parent / f"explicit-other-{tmp_path.name}"
    other.mkdir()
    registry_file = tmp_path / "registry.json"
    services = Services(workspace_root=home, registry=WorkspaceRegistry(registry_file))
    try:
        assert not registry_file.exists()

        workspace, created = services.workspaces.register(str(other), name="显式登记")

        assert created is True
        assert registry_file.exists()
        assert services.registry.get(str(other)).name == "显式登记"

        # The bound one is listed though unregistered: re-registering is a no-op duplicate
        existing, created_again = services.workspaces.register(str(home))
        assert created_again is False
        assert existing.id == services.workspaces.default.id
        assert services.registry.find(str(home)) is None
    finally:
        services.close()


def test_a_permission_field_is_rejected_without_registering_the_workspace(client, tmp_path):
    """A payload with permission is rejected at the schema layer (422) with no side effect: a
    rejected request must not register the workspace."""
    target = tmp_path.parent / f"invalid-{tmp_path.name}"
    target.mkdir()

    # The field itself does not exist (extra=forbid): manual/auto/full are all rejected
    for value in ("banana", "manual", "auto", "full"):
        rejected = client.post(
            "/api/workspaces", json={"path": str(target), "permission": value}
        )
        assert rejected.status_code == 422, rejected.text
    listed = client.get("/api/workspaces").json()["workspaces"]
    assert all(item["root"] != str(target) for item in listed), "被拒的请求不该留下登记"

    accepted = client.post("/api/workspaces", json={"path": str(target)})
    assert accepted.status_code == 201, accepted.text
    assert "default_permission" not in accepted.json()


# ---- session lookup cost ----


def test_session_lookup_scans_once_then_hits_the_cache(sandbox, monkeypatch):
    """Locating a session scans every workspace times file header once per run start, so a
    second lookup within the TTL must not scan again."""
    from avid.services import Services
    from avid.session import JsonlSessionRepo

    services = Services(root=sandbox / ".avid" / "sessions")
    try:
        first = services.sessions.create(workspace=services.workspaces.default.id)
        second = services.sessions.create(workspace=services.workspaces.default.id)

        scans: list[str] = []
        real_list = JsonlSessionRepo.list
        monkeypatch.setattr(
            JsonlSessionRepo, "list", lambda self: (scans.append("list"), real_list(self))[1]
        )

        # First call: clear the cache to simulate a new process
        services.workspaces._lookup.clear()
        workspace, meta = services.workspaces.repo_of_session(first["id"])
        assert meta.id == first["id"]
        assert len(scans) >= 1

        scans.clear()
        for _ in range(3):
            assert services.workspaces.repo_of_session(first["id"])[1].id == first["id"]
            assert services.workspaces.repo_of_session(second["id"])[1].id == second["id"]
        assert scans == [], "TTL 内不该再扫全库"
    finally:
        services.close()


def test_deleting_a_session_invalidates_its_lookup(sandbox):
    from avid.services import Services

    services = Services(root=sandbox / ".avid" / "sessions")
    try:
        created = services.sessions.create(workspace=services.workspaces.default.id)
        assert services.workspaces.find_session(created["id"]) is not None

        services.sessions.delete(created["id"])

        assert services.workspaces.find_session(created["id"]) is None, "删掉的会话不该被缓存命中"
    finally:
        services.close()
