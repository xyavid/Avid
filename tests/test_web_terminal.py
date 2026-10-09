"""Terminal WebSocket bridge: echo roundtrip, guards, and lifecycle.

TestClient performs a real handshake and the shell is the test process's SHELL, so an
echo roundtrip pins writing to the PTY and reading the output back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient, WebSocketDisconnect

from avid.services import Services
from avid.web import create_app


@pytest.fixture
def client(sandbox: Path):
    instance = Services(root=sandbox / ".avid" / "sessions")
    client = TestClient(
        create_app(services=instance, static_dir=sandbox / "static-not-built"),
        base_url="http://127.0.0.1:8765",
    )
    yield client
    # the workspace registry is user-level (~/.avid/workspaces.json); undo test registrations
    for item in client.get("/api/workspaces").json()["workspaces"]:
        if str(sandbox) in (item.get("root") or ""):
            client.delete(f"/api/workspaces/{item['id']}")


def _register_workspace(client: TestClient, path: Path) -> str:
    """Registers a workspace and returns its root; a 409 means the existing entry."""
    response = client.post("/api/workspaces", json={"path": str(path)})
    if response.status_code == 409:
        detail = response.json()["error"]["detail"]
        assert detail["root"] == str(path)
        return str(path)
    assert response.status_code == 201, response.text
    return response.json()["root"]


def _read_until(ws, needle: str, cap: int = 300) -> str:
    """Collects down frames until needle appears (the startup banner makes the frame count vary)."""
    seen: list[str] = []
    for _ in range(cap):
        frame = json.loads(ws.receive_text())
        if frame["type"] == "out":
            seen.append(frame["data"])
            if needle in "".join(seen):
                return "".join(seen)
        elif frame["type"] == "error":
            raise AssertionError(f"终端回错：{frame.get('message')}")
    raise AssertionError(f"{cap} 帧内没等到 {needle!r}（收到 {len(seen)} 帧输出）")


def test_terminal_echo_roundtrip_in_the_workspace(client: TestClient, sandbox: Path):
    """A command written to the PTY runs in the registered workspace and streams back as-is."""
    root = _register_workspace(client, sandbox)
    with client.websocket_connect(
        f"/api/ws/terminal?root={root}&cols=80&rows=24"
    ) as ws:
        ws.send_text(json.dumps({"type": "in", "data": "echo marker-$((6*7)); pwd\r"}))
        output = _read_until(ws, "marker-42")
        assert "marker-42" in output
        assert str(sandbox) in output


def test_terminal_refuses_an_unregistered_root(client: TestClient):
    """root must be a registered workspace: arbitrary directories get no shell."""
    with client.websocket_connect("/api/ws/terminal?root=/tmp/not-registered") as ws:
        frame = json.loads(ws.receive_text())
        assert frame["type"] == "error"
        assert "已登记工作区" in frame["message"]


def test_terminal_rejects_a_foreign_origin(client: TestClient, sandbox: Path):
    """WS bypasses TrustBoundaryMiddleware, so the Origin check is done here."""
    _register_workspace(client, sandbox)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/ws/terminal?root={sandbox}&cols=80&rows=24",
            headers={"origin": "http://evil.example"},
        ):
            pass  # rejected at the handshake: the connection never opens


def test_terminal_closing_the_socket_kills_the_shell(client: TestClient, sandbox: Path):
    """Lifecycle follows the connection: when the socket closes the shell process group must
    be reaped (no orphans)."""
    import subprocess

    root = _register_workspace(client, sandbox)
    marker_file = sandbox / "orphan-probe"
    with client.websocket_connect(f"/api/ws/terminal?root={root}&cols=80&rows=24") as ws:
        ws.send_text(json.dumps({"type": "in", "data": f"sleep 30 && touch {marker_file}\r"}))
        _read_until(ws, "$")  # wait for the prompt so sleep is already running
    import time

    time.sleep(0.5)
    assert not marker_file.exists(), "断开连接后 sleep 进程组没有被收割"
    # clean up a sleep that may still be running (belt and braces)
    subprocess.run(["pkill", "-f", "sleep 30"], check=False)


def test_terminal_survives_non_object_frames(client: TestClient, sandbox: Path):
    """Bad frames (numbers, strings) are skipped without killing the connection."""
    root = _register_workspace(client, sandbox)
    with client.websocket_connect(f"/api/ws/terminal?root={root}&cols=80&rows=24") as ws:
        for bad in ("5", "null", '"x"', "[1]"):
            ws.send_text(bad)
        ws.send_text(json.dumps({"type": "in", "data": "echo still-alive-$((7*6))\r"}))
        assert "still-alive-42" in _read_until(ws, "still-alive-42")


def test_terminal_rejects_cross_port_origin(client: TestClient, sandbox: Path):
    """A local page whose hostname is allowlisted but whose port differs may not drive the shell."""
    _register_workspace(client, sandbox)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/ws/terminal?root={sandbox}&cols=80&rows=24",
            headers={"origin": "http://127.0.0.1:9999"},
        ):
            pass
