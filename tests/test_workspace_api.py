"""工作区的 HTTP 面：候选列表、登记、建会话时的必选归属、运行级权限模式。

这一组用例覆盖阶段 18 的 Web 侧验收：**归属可查询**（会话列表带 workspace）、
**新建必须先选**（多工作区模式下缺 workspace 是 400）、**归属可持久化**
（会话落在该工作区的 .avid/sessions 下、header 里带着 workspaceId）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import ScriptedChat, make_turn

from avid.svc import Services
from avid.web import create_app
from avid.workspaces import WorkspaceRegistry


@pytest.fixture
def multi(tmp_path):
    """一个与仓库无关的工作地点 + 空注册表：建会话必须指定归属。

    ``workspace_root`` 指到临时目录是必须的：没有它 ``Services`` 会把进程默认根
    （跑测试时就是仓库根）当成工作地点登记进来，测试就会往仓库里写会话文件。
    """
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
    return TestClient(create_app(services=services, static_dir=sandbox / "unbuilt"))


def test_direct_sessions_root_still_binds_one_workplace(tmp_path):
    """显式给会话库路径（测试与 `avid web --workspace` 之外的装配）也要有工作地点。

    进程永远绑定一个工作地点，并且它可被解析（因此在候选列表里、标着 is_default）。
    """
    from fastapi.testclient import TestClient

    from avid.svc import Services
    from avid.web import create_app

    root = tmp_path.parent / f"direct-{tmp_path.name}" / ".avid" / "sessions"
    root.mkdir(parents=True)
    # 自己的注册表文件：同一测试里另一个 Services 不该出现在这份候选里。
    services = Services(root=root, registry=WorkspaceRegistry(tmp_path / "registry.json"))
    try:
        single = TestClient(create_app(services=services, static_dir="/tmp/unbuilt"))
        listed = single.get("/api/workspaces").json()["workspaces"]

        assert len(listed) == 1
        assert listed[0]["is_default"] is True
        assert listed[0]["id"].startswith("w-")
        # 归属可解析：用它建会话是 201，且会话落在给的那个库里。
        created = single.post("/api/sessions", json={"workspace": listed[0]["id"]})
        assert created.status_code == 201, created.text
        assert list(root.glob("*.jsonl"))
    finally:
        services.close()


def test_create_session_always_names_a_workspace(client):
    """进程自己绑定了工作地点，但**建会话仍必须显式指定**——绑定值只是预选项。

    省略会让归属取决于服务端状态而不是请求，而归属是会话的不可变事实。
    """
    listed = client.get("/api/workspaces").json()["workspaces"]
    assert any(ws["is_default"] for ws in listed)

    response = client.post("/api/sessions", json={"name": "没有归属"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "workspace_required"

    bound = next(ws for ws in listed if ws["is_default"])
    explicit = client.post("/api/sessions", json={"workspace": bound["id"]})
    assert explicit.status_code == 201, explicit.text


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
    # 进程自己绑定的工作地点也在候选里（is_default），所以断言"包含"而不是"只有它"。
    assert workspace["id"] in {item["id"] for item in listed}

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


def test_startup_writes_nothing_to_the_registry(tmp_path):
    """启动（含默认根）不写盘：注册表只由用户显式动作写入。

    绑定值照常出现在候选里、照常可解析（id 由根派生），只是没被登记——于是
    "看一眼注册表"与"起过服务"仍是可区分的两件事。
    """
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

        # 未登记但可解析：显式指定它的 id 就能建会话，会话落在它自己的会话库里。
        created = services.sessions.create(workspace=listed[0]["id"])
        assert created["workspace"]["id"] == listed[0]["id"]
        assert list((home / ".avid" / "sessions").glob("*.jsonl"))
        assert not registry_file.exists()  # 建会话也不写注册表
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

        # 绑定值的那个虽然没登记，也已经"在列表里"：再登记它算重复，不写盘、不重复添加。
        existing, created_again = services.workspaces.register(str(home))
        assert created_again is False
        assert existing.id == services.workspaces.default.id
        assert services.registry.find(str(home)) is None
    finally:
        services.close()


# ---------------- 任务板跟着工作区走 ----------------


def _seed_task(root, subject: str) -> None:
    """直接在某个工作区的 ``.tasks/`` 里放一条任务（模拟 agent 写过）。"""
    directory = Path(root) / ".tasks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "task_aaaaaaaa.json").write_text(
        json.dumps(
            {
                "id": "task_aaaaaaaa",
                "subject": subject,
                "description": "",
                "status": "pending",
                "owner": None,
                "blockedBy": [],
            }
        ),
        encoding="utf-8",
    )


def test_task_board_reads_the_workspace_not_the_process_cwd(client, multi, tmp_path):
    """任务库每个工作区一份；视图以前用模块级 TASKS（按进程 CWD 解析），
    多工作区模式下任务板显示的是另一个工作区的任务（阶段 18 漏掉的落点）。"""
    services, registry = multi
    bound = services.workspaces.default
    other_root = tmp_path.parent / f"tasks-other-{tmp_path.name}"
    other_root.mkdir()
    other = registry.add(other_root)
    _seed_task(bound.root, "绑定工作区的任务")
    _seed_task(other.root, "另一个工作区的任务")

    default_board = client.get("/api/tasks")
    assert default_board.status_code == 200, default_board.text
    assert [item["subject"] for item in default_board.json()["tasks"]] == [
        "绑定工作区的任务"
    ]

    # 显式指定：界面在多工作区模式下要能看别的工作区的任务板。
    explicit = client.get("/api/tasks", params={"workspace": other.id})
    assert explicit.status_code == 200, explicit.text
    assert [item["subject"] for item in explicit.json()["tasks"]] == [
        "另一个工作区的任务"
    ]

    # 单条任务同样按工作区解析：同一个 id 在两个工作区里是两条不同的任务。
    single = client.get("/api/tasks/task_aaaaaaaa", params={"workspace": other.id})
    assert single.status_code == 200, single.text
    assert single.json()["subject"] == "另一个工作区的任务"

    unknown = client.get("/api/tasks", params={"workspace": "w-nope"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "workspace_not_found"
