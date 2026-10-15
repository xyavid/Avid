"""Web tool settings: one resolution with env > settings file > default, the key in the same 0600
store as the model keys, and the /api/settings/web endpoints reading and writing exactly that.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from avid import web_settings
from avid.agent.tools import web as web_tools
from avid.providers import byok
from avid.security import userdirs
from avid.services import Services
from avid.web import create_app


@pytest.fixture
def client(tmp_path):
    services = Services(workspace_root=tmp_path)
    try:
        yield TestClient(
            create_app(services=services),
            base_url="http://127.0.0.1:8765",
        )
    finally:
        services.close()


def store(block: object) -> None:
    userdirs.write_settings({web_settings.SETTINGS_KEY: block})


# ---- the resolution ----


def test_defaults_when_nothing_is_configured():
    assert web_settings.resolved_search_url() == (web_settings.DEFAULT_SEARCH_URL, "default")
    assert web_settings.resolved_timeout() == (web_settings.DEFAULT_TIMEOUT_SECONDS, "default")
    assert web_settings.read_search_key() == ""
    assert web_settings.allow_private_hosts() is False


def test_the_settings_file_wins_over_the_default():
    store({"search_url": "https://search.internal/v1", "timeout_seconds": 12})

    assert web_settings.resolved_search_url() == ("https://search.internal/v1", "settings")
    assert web_settings.resolved_timeout() == (12.0, "settings")


def test_the_environment_wins_over_the_settings_file(monkeypatch):
    store({"search_url": "https://search.internal/v1", "timeout_seconds": 12})
    monkeypatch.setenv(web_settings.ENV_SEARCH_URL, "https://env.example/v2")
    monkeypatch.setenv(web_settings.ENV_TIMEOUT, "7")

    assert web_settings.resolved_search_url() == ("https://env.example/v2", "env")
    assert web_settings.resolved_timeout() == (7.0, "env")


@pytest.mark.parametrize(("asked", "expected"), [(0.1, 1.0), (9999, 300.0)])
def test_the_timeout_is_clamped(asked: float, expected: float):
    store({"timeout_seconds": asked})

    assert web_settings.resolved_timeout() == (expected, "settings")


@pytest.mark.parametrize("block", ["nope", 42, {"timeout_seconds": "soon"}, {"search_url": "  "}])
def test_a_malformed_block_reads_as_the_default(block: object):
    store(block)

    assert web_settings.resolved_search_url() == (web_settings.DEFAULT_SEARCH_URL, "default")
    assert web_settings.resolved_timeout() == (web_settings.DEFAULT_TIMEOUT_SECONDS, "default")


def test_a_malformed_env_timeout_still_owns_the_value(monkeypatch):
    """The UI must not claim the file decides while an env var is what actually decides."""
    store({"timeout_seconds": 12})
    monkeypatch.setenv(web_settings.ENV_TIMEOUT, "whenever")

    assert web_settings.resolved_timeout() == (web_settings.DEFAULT_TIMEOUT_SECONDS, "env")


def test_saving_nothing_removes_the_block_again():
    store({"search_url": "https://search.internal/v1"})

    web_settings.save_settings(search_url=None, timeout_seconds=None)

    assert web_settings.SETTINGS_KEY not in userdirs.read_settings()


def test_the_key_round_trips_through_the_secret_store():
    web_settings.write_search_key("tvly-abc")
    assert web_settings.read_search_key() == "tvly-abc"
    assert byok.read_secrets()[web_settings.SECRET_REF] == "tvly-abc"

    web_settings.delete_search_key()
    assert web_settings.read_search_key() == ""


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("true", True), ("on", True), ("0", False), ("", False)])
def test_allow_private_hosts_parses_the_switch(monkeypatch, raw: str, expected: bool):
    monkeypatch.setenv(web_settings.ENV_ALLOW_PRIVATE, raw)

    assert web_settings.allow_private_hosts() is expected


# ---- the endpoints ----


def test_get_reports_sources_and_never_the_key(client):
    byok.set_secret(web_settings.SECRET_REF, "tvly-secret")

    body = client.get("/api/settings/web").json()

    assert body["key_set"] is True
    assert "tvly-secret" not in str(body)
    assert body["search_url"] == web_settings.DEFAULT_SEARCH_URL
    assert body["search_url_source"] == "default"
    assert body["secrets_file"] == str(byok.secrets_path())
    assert body["signup_url"] == web_settings.SIGNUP_URL


def test_put_saves_the_key_the_endpoint_and_the_timeout(client):
    response = client.put(
        "/api/settings/web",
        json={"api_key": "tvly-abc", "search_url": "https://search.internal/v1", "timeout_seconds": 15},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["key_set"] is True
    assert body["search_url"] == "https://search.internal/v1"
    assert body["search_url_source"] == "settings"
    assert body["timeout_seconds"] == 15
    assert web_settings.read_search_key() == "tvly-abc"
    assert userdirs.read_settings()[web_settings.SETTINGS_KEY]["search_url"] == "https://search.internal/v1"


def test_put_leaves_the_stored_key_alone_when_api_key_is_null(client):
    byok.set_secret(web_settings.SECRET_REF, "tvly-keep")

    body = client.put("/api/settings/web", json={"search_url": "", "timeout_seconds": None}).json()

    assert body["key_set"] is True
    assert web_settings.read_search_key() == "tvly-keep"


def test_put_clears_the_key_on_an_empty_string(client):
    byok.set_secret(web_settings.SECRET_REF, "tvly-gone")

    body = client.put("/api/settings/web", json={"api_key": ""}).json()

    assert body["key_set"] is False
    assert web_settings.read_search_key() == ""


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"search_url": "search.internal"}, "协议头"),
        ({"timeout_seconds": 0.2}, "端到端"),
        ({"timeout_seconds": 9999}, "端到端"),
        ({"api_key": "tvly-键"}, "非 ASCII"),
        ({"api_key": "tvly-键", "search_url": "search.internal"}, "非 ASCII"),
    ],
)
def test_put_rejects_bad_input_before_writing_anything(client, payload: dict, needle: str):
    response = client.put("/api/settings/web", json=payload)

    assert response.status_code == 400, response.text
    assert userdirs.read_settings().get(web_settings.SETTINGS_KEY) is None
    assert web_settings.read_search_key() == ""


def test_a_rejected_put_does_not_leave_a_key_behind(client):
    """The key is written after validation, so a bad endpoint cannot half-apply the form."""
    client.put("/api/settings/web", json={"api_key": "tvly-abc", "search_url": "not-a-url"})

    assert web_settings.read_search_key() == ""
    assert userdirs.read_settings().get(web_settings.SETTINGS_KEY) is None


def test_put_refuses_a_field_the_environment_owns(client, monkeypatch):
    monkeypatch.setenv(web_settings.ENV_SEARCH_URL, "https://env.example/v2")

    refused = client.put("/api/settings/web", json={"search_url": "https://other.example/v1"})

    assert refused.status_code == 400
    assert web_settings.ENV_SEARCH_URL in refused.json()["error"]["message"]

    # Submitting the env value itself is accepted, and is not copied into the settings file.
    accepted = client.put("/api/settings/web", json={"search_url": "https://env.example/v2"})
    assert accepted.status_code == 200
    assert userdirs.read_settings().get(web_settings.SETTINGS_KEY) is None


def test_test_endpoint_without_a_key_does_not_call_anything(client, monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("no key means no request")

    monkeypatch.setattr(web_tools, "search_once", explode)

    body = client.post("/api/settings/web/test", json={}).json()

    assert body["ok"] is False
    assert "Key" in body["message"]


def test_test_endpoint_reports_a_tool_failure_verbatim(client, monkeypatch):
    byok.set_secret(web_settings.SECRET_REF, "tvly-abc")

    monkeypatch.setattr(web_tools, "search_once", lambda *a, **k: "错误：联网检索失败（HTTP 401 — nope）。")

    body = client.post("/api/settings/web/test", json={}).json()

    assert body["ok"] is False
    assert "401" in body["message"]


def test_test_endpoint_runs_the_same_core_with_the_stored_values(client, monkeypatch):
    byok.set_secret(web_settings.SECRET_REF, "tvly-abc")
    store({"search_url": "https://search.internal/v1", "timeout_seconds": 9})
    seen: dict = {}

    def fake(query, *, key, url, max_results, client=None):
        seen.update(query=query, key=key, url=url, max_results=max_results)
        return "检索「x」：共 1 条结果\n1. hit"

    monkeypatch.setattr(web_tools, "search_once", fake)

    body = client.post("/api/settings/web/test", json={}).json()

    assert body["ok"] is True
    assert "连得上" in body["message"]
    assert seen["key"] == "tvly-abc"
    assert seen["url"] == "https://search.internal/v1"
    assert seen["max_results"] == 1
