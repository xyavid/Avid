"""终端桥的 WebSocket 用例：回环、守卫、生命周期。

TestClient 的 websocket_connect 走真实握手；shell 用测试进程的 SHELL，
echo 回环足以钉住「写入到 PTY → 读回输出」的主链路。
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
    # 工作区注册表是用户级的（~/.avid/workspaces.json），测试登记过的要拆掉
    for item in client.get("/api/workspaces").json()["workspaces"]:
        if str(sandbox) in (item.get("root") or ""):
            client.delete(f"/api/workspaces/{item['id']}")


def _register_workspace(client: TestClient, path: Path) -> str:
    """登记工作区并返回其根目录；重复登记（409）按既有条目处理。"""
    response = client.post("/api/workspaces", json={"path": str(path)})
    if response.status_code == 409:
        detail = response.json()["error"]["detail"]
        assert detail["root"] == str(path)
        return str(path)
    assert response.status_code == 201, response.text
    return response.json()["root"]


def _read_until(ws, needle: str, cap: int = 300) -> str:
    """收集下行帧直到出现 needle（bash 启动横幅在前，帧数不固定）。"""
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
    """写入 PTY 的命令在登记的工作区里执行，输出原样流回。"""
    root = _register_workspace(client, sandbox)
    with client.websocket_connect(
        f"/api/ws/terminal?root={root}&cols=80&rows=24"
    ) as ws:
        ws.send_text(json.dumps({"type": "in", "data": "echo marker-$((6*7)); pwd\r"}))
        output = _read_until(ws, "marker-42")
        assert "marker-42" in output
        assert str(sandbox) in output


def test_terminal_refuses_an_unregistered_root(client: TestClient):
    """root 必须是已登记工作区：任意目录不给开 shell。"""
    with client.websocket_connect("/api/ws/terminal?root=/tmp/not-registered") as ws:
        frame = json.loads(ws.receive_text())
        assert frame["type"] == "error"
        assert "已登记工作区" in frame["message"]


def test_terminal_rejects_a_foreign_origin(client: TestClient, sandbox: Path):
    """WS 不走 TrustBoundaryMiddleware，Origin 校验自己补。"""
    _register_workspace(client, sandbox)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/ws/terminal?root={sandbox}&cols=80&rows=24",
            headers={"origin": "http://evil.example"},
        ):
            pass  # 握手即被拒：连接根本建立不起来


def test_terminal_closing_the_socket_kills_the_shell(client: TestClient, sandbox: Path):
    """生命周期跟连接走：socket 断开，shell 进程组必须被收割（不留孤儿）。"""
    import subprocess

    root = _register_workspace(client, sandbox)
    marker_file = sandbox / "orphan-probe"
    with client.websocket_connect(f"/api/ws/terminal?root={root}&cols=80&rows=24") as ws:
        ws.send_text(json.dumps({"type": "in", "data": f"sleep 30 && touch {marker_file}\r"}))
        _read_until(ws, "$")  # 等提示符出现再断开（sleep 已在跑）
    import time

    time.sleep(0.5)
    assert not marker_file.exists(), "断开连接后 sleep 进程组没有被收割"
    # 清理可能还在跑的 sleep（组被杀则它早死了；双保险）
    subprocess.run(["pkill", "-f", "sleep 30"], check=False)
