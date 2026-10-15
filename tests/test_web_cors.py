"""Cross-origin access: which Origin is granted, what the grant contains, and the preflight.

The API serves no page of its own, so a browser reaches it from an origin this process never sees
in the Host header. Four outcomes are pinned here — served ungranted (no Origin), granted, refused,
and preflighted — because they are four halves of one decision and a change to any one of them
could quietly widen another.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from avid.services import Services
from avid.web import create_app
from avid.web.app import (
    ALLOWED_ORIGINS_ENV,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    declared_origins,
    trusted_hosts,
    trusted_origins,
)

#: A page on the loopback interface: the zero-config case, and the one a dev server produces.
LOOPBACK_PAGE = "http://localhost:5173"
#: A page somewhere this process was never addressed. Nothing should reach it without being named.
FOREIGN_PAGE = "http://evil.example"


@pytest.fixture
def client(sandbox: Path):
    services = Services(root=sandbox / ".avid" / "sessions")
    built = TestClient(create_app(services=services), base_url="http://127.0.0.1:8765")
    try:
        yield built
    finally:
        services.close()


# ---------------- the four outcomes ----------------


def test_a_command_line_client_gets_no_grant(client: TestClient):
    """No Origin means no browser is involved, so there is nothing to grant and nothing to refuse."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_an_allowed_origin_is_granted_the_response(client: TestClient):
    response = client.get("/api/health", headers={"Origin": LOOPBACK_PAGE})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == LOOPBACK_PAGE


def test_an_unlisted_origin_is_refused(client: TestClient):
    response = client.get("/api/health", headers={"Origin": FOREIGN_PAGE})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"


def test_the_preflight_is_answered_without_reaching_a_route(client: TestClient):
    """No route declares OPTIONS, so the middleware has to answer the preflight itself."""
    response = client.options(
        "/api/sessions",
        headers={
            "Origin": LOOPBACK_PAGE,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == LOOPBACK_PAGE
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "Content-Type" in response.headers["access-control-allow-headers"]
    assert int(response.headers["access-control-max-age"]) > 0


def test_the_response_varies_by_origin(client: TestClient):
    """Without Vary a cache could hand a granted answer to a page that would not have been granted."""
    assert client.get("/api/health", headers={"Origin": LOOPBACK_PAGE}).headers["vary"] == "Origin"
    assert client.get("/api/health").headers["vary"] == "Origin"


def test_the_host_allowlist_is_checked_before_the_origin_one(client: TestClient):
    """A foreign Host is refused whatever the Origin says: that check is the DNS-rebinding half."""
    response = client.get("/api/health", headers={"Host": "evil.example", "Origin": LOOPBACK_PAGE})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "host_rejected"


# ---------------- the grant is no wider than the caller ----------------


def test_the_grant_never_widens_to_a_wildcard():
    """A wildcard method list would grant verbs no route declares, and hide the next one added."""
    assert "*" not in CORS_ALLOW_METHODS
    assert "*" not in CORS_ALLOW_HEADERS
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert method in CORS_ALLOW_METHODS


# ---------------- two allowlists, two questions ----------------


def test_origin_and_host_allowlists_answer_different_questions(monkeypatch):
    """A name the process answers to need not be a page allowed to call it, and the reverse.

    A reverse proxy makes the two differ in practice: Host is the internal name, Origin the public
    site. Folding them back into one list would silently grant whatever the Host list happens to
    hold, so the split is asserted rather than assumed.
    """
    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, "page.example")
    assert "page.example" in trusted_origins()
    assert "page.example" not in trusted_hosts()


def test_loopback_pages_are_granted_by_default(monkeypatch):
    """Zero config for the local case: a dev server on any loopback port works as it stands."""
    for name in ("AVID_ALLOWED_HOSTS", ALLOWED_ORIGINS_ENV):
        monkeypatch.delenv(name, raising=False)
    assert "localhost" in trusted_origins()
    assert "127.0.0.1" in trusted_origins()


def test_declared_entries_are_reduced_to_hostnames(monkeypatch):
    """Entries are compared as hostnames, so a port or a scheme in the list cannot break matching."""
    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, " https://page.example:5173 , other.example ")
    assert {"page.example", "other.example"} <= trusted_origins()


# ---------------- the terminal is one tier stricter ----------------


def test_the_terminal_does_not_inherit_the_loopback_default(monkeypatch):
    """A PTY is a shell: it does not get the convenience that lets any loopback page read sessions.

    Losing the loopback default is only acceptable because the failure is visible — the panel shows
    the server's refusal — and because one declared name brings it back.
    """
    for name in ("AVID_ALLOWED_HOSTS", ALLOWED_ORIGINS_ENV):
        monkeypatch.delenv(name, raising=False)
    assert "localhost" in trusted_origins()
    assert declared_origins() == frozenset()

    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, "localhost")
    assert "localhost" in declared_origins()
