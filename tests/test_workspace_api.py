"""工作区的 HTTP 面：候选列表、登记、建会话时的必选归属、运行级权限模式。

这一组用例覆盖阶段 18 的 Web 侧验收：**归属可查询**（会话列表带 workspace）、
**新建必须先选**（多工作区模式下缺 workspace 是 400）、**归属可持久化**
（会话落在该工作区的 .avid/sessions 下、header 里带着 workspaceId）。
"""

from __future__ import annotations

import pytest

from avid.svc import Services
from avid.workspaces import WorkspaceRegistry
from avid.web import create_app

from support import ScriptedChat, make_turn


@pytest.fixture
def multi(sandbox):
    """多工作区模式：没有默认工作区，所以建会话必须指定归属。"""
    registry = WorkspaceRegistry()
    services = Services(registry=registry, chat=ScriptedChat([make_turn("答")]))
    yield services, registry
    services.close()


@pytest.fixture
def client(multi, sandbox):
    from fastapi.testclient import TestClient

    services, registry = multi
    return TestClient(create_app(services=services, static_dir=sandbox / "unbuilt"))


def test_single_workspace_mode_lists_the_default(client):
    """单工作区模式（测试与 `avid web --workspace`）下，列表里就有那一个。"""
    from fastapi.testclient import TestClient

    from avid.svc import Services
    from avid.web import create_app

    services = Services(root="/tmp/does-not-matter/sessions")
    try:
        single = TestClient(create_app(services=services, static_dir="/tmp/unbuilt"))
        listed = single.get("/api/workspaces").json()["workspaces"]

        assert len(listed) == 1
        assert listed[0]["is_default"] is True
        assert listed[0]["id"].startswith("w-")
    finally:
        services.close()


def test_create_session_requires_a_workspace_in_multi_mode(client):
    response = client.post("/api/sessions", json={"name": "没有归属"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "workspace_required"


def test_create_session_rejects_an_unknown_field(client):
    """未知字段是 422 而不是静默按默认值跑（extra=forbid 的理由）。"""
    response = client.post("/api/sessions", json={"workspace": "w-x", "permissions": "system"})

    assert response.status_code == 422


def test_register_then_create_a_session_in_that_workspace(client, sandbox, tmp_path):
    project = tmp_path.parent / f"proj-{tmp_path.name}"
    project.mkdir()

    created = client.post(
        "/api/workspaces", json={"path": str(project), "name": "项目", "permission": "workspace"}
    )
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert workspace["name"] == "项目"
    assert workspace["default_permission"] == "workspace"

    listed = client.get("/api/workspaces").json()["workspaces"]
    assert [item["id"] for item in listed] == [workspace["id"]]

    session = client.post("/api/sessions", json={"workspace": workspace["id"]})
    assert session.status_code == 201, session.text
    detail = session.json()
    assert detail["workspace"]["id"] == workspace["id"]
    assert detail["workspace"]["root"] == workspace["root"]

    # 归属可持久化：会话文件真的落在那个工作区的会话库里，header 带 workspaceId。
    files = list((project / ".avid" / "sessions").glob("*.jsonl"))
    assert len(files) == 1
    assert f'"workspaceId": "{workspace["id"]}"' in files[0].read_text(encoding="utf-8")

    # 列表也带归属，且能按 id 查回来。
    summary = client.get("/api/sessions").json()["sessions"][0]
    assert summary["workspace"]["id"] == workspace["id"]
    assert client.get(f"/api/sessions/{detail['id']}").json()["workspace"]["id"] == workspace["id"]


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


def test_run_records_the_workspace_and_permission(client, tmp_path, sandbox):
    project = tmp_path.parent / f"run-{tmp_path.name}"
    project.mkdir()
    ws = client.post(
        "/api/workspaces", json={"path": str(project), "permission": "strict"}
    ).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    started = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": "问题", "permission": "system", "auto_approve": True},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    # run_started 带上归属与模式：刷新页面后重建界面靠它（不能只存在内存里）。
    stream = client.get(f"/api/runs/{run_id}/events").text
    assert '"workspace"' in stream
    assert ws["id"] in stream
    assert '"permission"' in stream and "system" in stream


def test_run_permission_defaults_to_the_workspace_default(client, tmp_path, sandbox):
    project = tmp_path.parent / f"default-{tmp_path.name}"
    project.mkdir()
    ws = client.post(
        "/api/workspaces", json={"path": str(project), "permission": "workspace"}
    ).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    started = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": "问题", "auto_approve": True},
    )
    run_id = started.json()["run_id"]

    stream = client.get(f"/api/runs/{run_id}/events").text
    assert '"permission": "workspace"' in stream


def test_run_rejects_an_unknown_permission(client, tmp_path):
    project = tmp_path.parent / f"bad-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    session = client.post("/api/sessions", json={"workspace": ws["id"]}).json()

    response = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": "问题", "permission": "yolo"},
    )

    assert response.status_code == 422
