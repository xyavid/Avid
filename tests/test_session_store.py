"""Sessions-dir resolution (env > settings file > default) and settings-file write discipline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import create_session

from avid.security import userdirs


@pytest.fixture
def home(tmp_path, monkeypatch):
    """AVID_HOME points at a temp dir (set by conftest; the path is fetched explicitly here)."""
    return userdirs.avid_home()


def test_default_store_sits_under_the_avid_home(home):
    assert userdirs.sessions_dir() == home / "sessions"
    assert userdirs.sessions_dir_source() == "default"
    assert userdirs.default_sessions_dir() == home / "sessions"


def test_settings_file_wins_over_the_default(home, tmp_path):
    custom = tmp_path / "elsewhere" / "sessions"
    userdirs.write_settings({"sessions_dir": str(custom)})

    assert userdirs.sessions_dir() == custom
    assert userdirs.sessions_dir_source() == "settings"
    # The default is still reported: the UI needs to show where "restore" lands.
    assert userdirs.default_sessions_dir() == home / "sessions"
    assert userdirs.sessions_dir() != userdirs.default_sessions_dir()


def test_env_wins_over_the_settings_file(home, tmp_path, monkeypatch):
    monkeypatch.setenv(userdirs.SESSIONS_DIR_ENV, str(tmp_path / "from-env"))
    userdirs.write_settings({"sessions_dir": str(tmp_path / "from-file")})

    assert userdirs.sessions_dir() == tmp_path / "from-env"
    assert userdirs.sessions_dir_source() == "env"


def test_blank_values_fall_through(monkeypatch):
    monkeypatch.setenv(userdirs.SESSIONS_DIR_ENV, "   ")
    userdirs.write_settings({"sessions_dir": "   "})

    assert userdirs.sessions_dir_source() == "default"


def test_paths_expand_user(monkeypatch):
    monkeypatch.delenv(userdirs.SESSIONS_DIR_ENV, raising=False)
    userdirs.write_settings({"sessions_dir": "~/some-sessions"})

    assert userdirs.sessions_dir() == Path("~/some-sessions").expanduser()


def test_missing_or_corrupt_settings_read_as_empty(home):
    assert userdirs.read_settings() == {}

    userdirs.settings_path().parent.mkdir(parents=True, exist_ok=True)
    userdirs.settings_path().write_text("{ 不是 JSON", encoding="utf-8")
    assert userdirs.read_settings() == {}
    assert userdirs.sessions_dir() == home / "sessions"

    userdirs.settings_path().write_text('["列表不算设置"]', encoding="utf-8")
    assert userdirs.read_settings() == {}


def test_write_settings_preserves_sibling_keys_and_removes_on_none(home):
    userdirs.write_settings({"sessions_dir": "/tmp/one", "other": 7})
    assert userdirs.read_settings() == {"version": 1, "sessions_dir": "/tmp/one", "other": 7}

    userdirs.write_settings({"sessions_dir": None})
    assert userdirs.read_settings() == {"version": 1, "other": 7}
    assert userdirs.sessions_dir() == home / "sessions"
    assert json.loads(userdirs.settings_path().read_text(encoding="utf-8"))["other"] == 7


def test_write_settings_leaves_no_temp_file(home):
    userdirs.write_settings({"sessions_dir": "/tmp/two"})

    leftovers = [p.name for p in userdirs.settings_path().parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_write_settings_merges_only_the_given_keys(home):
    userdirs.write_settings({"sessions_dir": "/tmp/three"})
    userdirs.write_settings({})

    assert userdirs.read_settings()["sessions_dir"] == "/tmp/three"


# ---- HTTP surface (the two settings endpoints) ----


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from avid.services import Services
    from avid.web import create_app

    services = Services(workspace_root=tmp_path)
    try:
        yield TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
    finally:
        services.close()


def test_get_reports_the_default_store(client, home):
    body = client.get("/api/settings/sessions").json()

    assert body == {
        "dir": str(home / "sessions"),
        "default_dir": str(home / "sessions"),
        "source": "default",
        "editable": True,
    }


def test_put_moves_the_store_and_new_sessions_follow(client, tmp_path, home):
    """Changing the setting only affects where new sessions go: the dir is created on the spot."""
    target = tmp_path / "on-another-disk"
    assert not target.exists()

    saved = client.put("/api/settings/sessions", json={"dir": str(target)})

    assert saved.status_code == 200, saved.text
    assert saved.json()["dir"] == str(target)
    assert saved.json()["source"] == "settings"
    assert target.is_dir()

    created = create_session(client)
    assert created.status_code == 201, created.text
    workspace = created.json()["workspace"]["id"]
    assert list((target / workspace).glob("*.jsonl"))
    assert not (home / "sessions").exists()


def test_put_an_empty_dir_restores_the_default(client, tmp_path, home):
    custom = tmp_path / "custom"
    client.put("/api/settings/sessions", json={"dir": str(custom)})

    restored = client.put("/api/settings/sessions", json={"dir": ""})

    assert restored.json() == {
        "dir": str(home / "sessions"),
        "default_dir": str(home / "sessions"),
        "source": "default",
        "editable": True,
    }
    assert "sessions_dir" not in userdirs.read_settings()


def test_put_rejects_a_relative_path(client):
    response = client.put("/api/settings/sessions", json={"dir": "relative/dir"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "绝对路径" in response.json()["error"]["message"]


def test_put_refuses_while_the_environment_wins(client, tmp_path, monkeypatch):
    monkeypatch.setenv(userdirs.SESSIONS_DIR_ENV, str(tmp_path / "from-env"))

    read = client.get("/api/settings/sessions").json()
    assert read["source"] == "env" and read["editable"] is False

    written = client.put("/api/settings/sessions", json={"dir": str(tmp_path / "ignored")})
    assert written.status_code == 400
    assert userdirs.SESSIONS_DIR_ENV in written.json()["error"]["message"]
    assert not (tmp_path / "ignored").exists()


# ---- changing the store must not interrupt a live run ----


def test_changing_the_store_is_refused_while_a_run_is_live(tmp_path):
    """A live run holds session handles opened from the cached repo: rebinding closes them and the
    model's reply is lost."""
    import threading

    from fastapi.testclient import TestClient
    from support import create_session

    from avid.services import Services
    from avid.web import create_app

    started = threading.Event()
    release = threading.Event()

    def blocking_chat(config, messages, **kwargs):
        started.set()
        assert release.wait(10), "测试没有放开这次模型调用"
        from support import make_turn

        return make_turn("答")

    services = Services(workspace_root=tmp_path, chat=blocking_chat)
    try:
        client = TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        session_id = create_session(client).json()["id"]
        run = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "问"})
        assert run.status_code == 201, run.text
        assert started.wait(10), "run 没跑起来"

        refused = client.put("/api/settings/sessions", json={"dir": str(tmp_path / "moved")})

        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "session_busy"
        assert "运行" in refused.json()["error"]["message"]

        release.set()
        assert wait_for_run(client, run.json()["run_id"]), "run 没结束"
        # The reply is not lost: both messages of this run landed in the session file (old store).
        detail = client.get(f"/api/sessions/{session_id}").json()
        assert detail["message_count"] >= 2

        allowed = client.put("/api/settings/sessions", json={"dir": str(tmp_path / "moved")})
        assert allowed.status_code == 200, allowed.text
    finally:
        release.set()
        services.close()


def wait_for_run(client, run_id: str, timeout: float = 5.0) -> bool:
    from support import wait_for

    return wait_for(
        lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("finished", "failed", "cancelled"),
        timeout,
    )


def test_a_busy_services_reports_session_busy_not_a_crash(tmp_path):
    """The direct Services path behaves the same; the route is just one caller."""
    from avid.services import Services
    from avid.services.errors import SessionBusy

    services = Services(workspace_root=tmp_path)
    try:
        runs = services.runs
        with runs._lock:
            runs._active["s-fake"] = "run-fake"
        try:
            try:
                services.rebind_session_store()
            except SessionBusy as exc:
                assert "run-fake" in str(exc) or "运行" in str(exc)
            else:  # pragma: no cover - reaching here means the guard is missing
                raise AssertionError("有活动 run 时不该允许解绑")
        finally:
            with runs._lock:
                runs._active.pop("s-fake", None)
        services.rebind_session_store()  # with no active run, rebinding is allowed
    finally:
        services.close()
