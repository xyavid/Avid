"""The system folder picker: backend probing plus the "add workspace" HTTP path.

``AVID_PICKER_CMD`` overrides the backend and prints the chosen path on stdout; a real dialog
cannot open in tests, so the HTTP layer monkeypatches ``svc.workspaces.pick_directory``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, create_session, make_turn

from avid.services import Services
from avid.services import picker as picker_module
from avid.services.picker import PickerFailed, PickerUnavailable, pick_directory
from avid.services.workspace_registry import WorkspaceRegistry, sessions_root
from avid.web import create_app

# ---------------- backend ----------------

def test_override_backend_returns_the_printed_path():
    chosen = pick_directory(env={"AVID_PICKER_CMD": "printf %s /tmp/chosen"})

    assert chosen == "/tmp/chosen"


def test_empty_output_means_cancelled():
    assert pick_directory(env={"AVID_PICKER_CMD": "true"}) is None


def test_nonzero_exit_means_cancelled_not_failed():
    """Cancel and failure must stay distinct: zenity and kdialog both exit 1 on cancel."""
    assert pick_directory(env={"AVID_PICKER_CMD": "false"}) is None


def test_broken_override_command_is_a_failure():
    with pytest.raises(PickerFailed):
        pick_directory(env={"AVID_PICKER_CMD": "sh -c 'exit 3' && 'unclosed"})


def test_no_backend_at_all_is_unavailable_with_an_actionable_message(monkeypatch):
    monkeypatch.setattr(picker_module, "BACKENDS", ())

    with pytest.raises(PickerUnavailable) as exc:
        pick_directory(env={})

    assert "avid workspace add" in str(exc.value)


def test_backends_fall_through_in_order(monkeypatch):
    """A backend that fails to start is only noted, then the next one is tried."""
    calls = []

    def dead(timeout, env):
        calls.append("dead")
        raise picker_module._BackendUnavailable("连不上显示")

    def alive(timeout, env):
        calls.append("alive")
        return "/tmp/from-alive"

    monkeypatch.setattr(picker_module, "BACKENDS", (("dead", dead), ("alive", alive)))

    assert pick_directory(env={}) == "/tmp/from-alive"
    assert calls == ["dead", "alive"]


def test_available_backend_reports_the_override(monkeypatch):
    picker_module.clear_backend_cache()
    monkeypatch.setenv("AVID_PICKER_CMD", "printf %s /tmp/x")
    try:
        assert picker_module.available_backend() == "override"
    finally:
        picker_module.clear_backend_cache()


def test_available_backend_is_none_when_nothing_is_left(monkeypatch):
    picker_module.clear_backend_cache()
    monkeypatch.setattr(picker_module, "BACKENDS", ())
    try:
        assert picker_module.available_backend() is None
    finally:
        picker_module.clear_backend_cache()


# ---------------- HTTP ----------------

@pytest.fixture
def bundle(tmp_path):
    """A service and client that never touch the real home directory."""
    home = tmp_path.parent / f"picker-home-{tmp_path.name}"
    home.mkdir()
    services = Services(
        workspace_root=home,
        registry=WorkspaceRegistry(tmp_path / "registry.json"),
        chat=ScriptedChat([make_turn("答")]),
    )
    client = TestClient(
        create_app(services=services, static_dir=tmp_path / "unbuilt"),
        base_url="http://127.0.0.1:8765",
    )
    yield client, services, tmp_path
    services.close()


@pytest.fixture
def picked(tmp_path, monkeypatch):
    """Replace the picker with one returning a fixed path, so no real dialog opens."""
    chosen = tmp_path.parent / f"picked-{tmp_path.name}"
    chosen.mkdir()

    def fake() -> str | None:
        return str(chosen)

    monkeypatch.setattr("avid.services.workspaces.pick_directory", fake)
    return chosen


def test_pick_returns_the_chosen_folder(bundle, picked):
    client, _, _ = bundle

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 200
    assert response.json() == {"path": str(picked)}


def test_pick_cancel_changes_nothing(bundle, monkeypatch):
    client, services, _ = bundle
    before = services.workspaces.list()
    monkeypatch.setattr("avid.services.workspaces.pick_directory", lambda: None)

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 200
    assert response.json() == {"path": None}
    assert services.workspaces.list() == before  # a cancel changes nothing


def test_pick_without_a_backend_is_503_with_the_manual_way_out(bundle, monkeypatch):
    client, _, _ = bundle

    def unavailable():
        raise PickerUnavailable("这台机器上没有可用的系统文件夹选择器。")

    monkeypatch.setattr("avid.services.workspaces.pick_directory", unavailable)

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "picker_unavailable"


def test_pick_while_one_is_open_is_409(bundle, picked):
    client, services, _ = bundle

    with services.workspaces._pick_lock:  # simulate a dialog already being open
        response = client.post("/api/workspaces/pick")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "picker_busy"


def test_picked_path_that_vanished_is_rejected(bundle, monkeypatch, tmp_path):
    client, _, _ = bundle
    monkeypatch.setattr(
        "avid.services.workspaces.pick_directory", lambda: str(tmp_path / "vanished")
    )

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "workspace_invalid"


def test_add_then_it_appears_in_the_list_and_in_the_registry(bundle, picked):
    client, services, tmp_path = bundle

    created = client.post("/api/workspaces", json={"path": str(picked), "name": "新项目"})

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "新项目"
    assert body["root"] == str(picked.resolve())

    listed = client.get("/api/workspaces").json()["workspaces"]
    assert body["id"] in {item["id"] for item in listed}
    # persisted: the registry file exists and a fresh read still finds it
    assert services.registry.find(body["id"]) is not None
    assert (tmp_path / "registry.json").exists()


def test_adding_the_same_folder_twice_is_409_and_does_not_duplicate(bundle, picked):
    client, services, _ = bundle
    first = client.post("/api/workspaces", json={"path": str(picked)}).json()

    again = client.post("/api/workspaces", json={"path": str(picked)})

    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "workspace_exists"
    assert error["detail"]["id"] == first["id"]
    # not added twice: it appears once in the list
    ids = [item["id"] for item in client.get("/api/workspaces").json()["workspaces"]]
    assert ids.count(first["id"]) == 1


def test_adding_the_process_bound_folder_is_409_too(bundle):
    """The process-bound folder is a candidate too, so it counts as already existing."""
    client, services, _ = bundle

    response = client.post(
        "/api/workspaces", json={"path": services.workspaces.default.root}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_exists"
    assert services.registry.find(services.workspaces.default.id) is None


def test_adding_a_missing_folder_is_400(bundle, tmp_path):
    client, _, _ = bundle

    response = client.post("/api/workspaces", json={"path": str(tmp_path / "nope")})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "workspace_invalid"


# ---------------- interaction with switching ----------------

def test_new_workspace_is_usable_for_a_session_right_away(bundle, picked):
    """Switching in the UI means selecting it; the server guarantees it is usable right away."""
    client, _, _ = bundle
    created = client.post("/api/workspaces", json={"path": str(picked)}).json()

    session = create_session(client, workspace=created["id"])

    assert session.status_code == 201, session.text
    assert session.json()["workspace"]["id"] == created["id"]
    assert list(sessions_root(picked).glob("*.jsonl"))


def test_meta_exposes_the_picker_backend(bundle, monkeypatch):
    """The diagnostics field must truly reach /api/meta: a pydantic DTO silently drops keys
    the schema does not declare, which would show a working picker as absent.
    """
    client, _, _ = bundle
    monkeypatch.setattr("avid.services.available_backend", lambda: "tkinter")

    capabilities = client.get("/api/meta").json()["capabilities"]

    assert capabilities["workspace_picker"] == "tkinter"


def test_a_timing_out_picker_is_a_picker_failure_not_a_500(monkeypatch):
    """A picker that starts but never returns must time out as PickerFailed, never as a 500."""
    monkeypatch.setenv(picker_module.ENV_OVERRIDE, "sleep 30")

    with pytest.raises(picker_module.PickerFailed):
        picker_module.pick_directory(timeout=0.5)


def test_the_diagnostics_value_is_not_cached_forever(monkeypatch):
    """Backend diagnostics must refresh without a restart: the cache only holds within its TTL."""
    monkeypatch.setenv(picker_module.ENV_OVERRIDE, "")
    picker_module.clear_backend_cache()
    probed = picker_module.available_backend()
    assert probed != "override", "没设覆盖命令时不该报 override"

    monkeypatch.setenv(picker_module.ENV_OVERRIDE, "/bin/true")
    assert picker_module.available_backend() != "override", "TTL 内返回缓存值（免重复探测）"

    picker_module.clear_backend_cache()
    assert picker_module.available_backend() == "override", "清掉缓存后立刻看到新值"
