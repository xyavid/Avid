"""工作区的 HTTP 面：候选列表、登记、建会话时的必选归属、运行级完全访问凭据。

这一组用例覆盖阶段 18 的 Web 侧验收：**归属可查询**（会话列表带 workspace）、
**新建必须先选**（多工作区模式下缺 workspace 是 400）、**归属可持久化**
（会话落在共享会话目录里该工作区的子目录下、header 里带着 workspaceId）。
阶段 51 后权限只有一个运行级取值：默认 normal，`full_access_ack` 是完全访问凭据。
"""

from __future__ import annotations

import pytest
from support import ScriptedChat, make_turn

from avid.services import Services
from avid.services.workspace_registry import WorkspaceRegistry, sessions_root
from avid.web import create_app


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
    return TestClient(
        create_app(services=services, static_dir=sandbox / "unbuilt"),
        base_url="http://127.0.0.1:8765",
    )


def test_direct_sessions_root_still_binds_one_workplace(tmp_path):
    """显式给会话库路径（测试与 `avid web --workspace` 之外的装配）也要有工作地点。

    进程永远绑定一个工作地点，并且它可被解析（因此在候选列表里、标着 is_default）。
    """
    from fastapi.testclient import TestClient

    from avid.services import Services
    from avid.web import create_app

    root = tmp_path.parent / f"direct-{tmp_path.name}" / ".avid" / "sessions"
    root.mkdir(parents=True)
    # 自己的注册表文件：同一测试里另一个 Services 不该出现在这份候选里。
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

    created = client.post("/api/workspaces", json={"path": str(project), "name": "项目"})
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert workspace["name"] == "项目"
    # 工作区不再携带默认权限：注册载荷带上它是 422，回显里也没有这个键。
    assert "default_permission" not in workspace

    listed = client.get("/api/workspaces").json()["workspaces"]
    # 进程自己绑定的工作地点也在候选里（is_default），所以断言"包含"而不是"只有它"。
    assert workspace["id"] in {item["id"] for item in listed}

    session = client.post("/api/sessions", json={"workspace": workspace["id"]})
    assert session.status_code == 201, session.text
    detail = session.json()
    assert detail["workspace"]["id"] == workspace["id"]
    assert detail["workspace"]["root"] == workspace["root"]

    # 归属可持久化：会话文件真的落在专用会话目录里那个工作区的子目录下，header 带 workspaceId。
    files = list(sessions_root(project).glob("*.jsonl"))
    assert len(files) == 1
    assert f'"workspaceId": "{workspace["id"]}"' in files[0].read_text(encoding="utf-8")

    # 列表也带归属，且能按 id 查回来。
    summary = client.get("/api/sessions").json()["sessions"][0]
    assert summary["workspace"]["id"] == workspace["id"]
    assert client.get(f"/api/sessions/{detail['id']}").json()["workspace"]["id"] == workspace["id"]


def test_delete_a_workspace_keeps_its_sessions_listed(client, tmp_path):
    """删工作区 = 从候选里摘掉。它的会话仍在列表里（界面据此归到「未归属的会话」），
    也照旧打得开——否则"删一个工作区"就等于悄悄销毁了里面的全部会话。"""
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
    # 归属仍是那个 id：前端按"候选里找不到它"把这条会话归进未归属组。
    assert listed[0]["workspace"]["id"] == ws["id"]
    assert client.get(f"/api/sessions/{session['id']}").status_code == 200
    assert sessions_root(project).exists()


def test_delete_the_bound_workspace_is_refused(client):
    """进程绑定的工作地点永远在候选里（它不进注册表），删它只会"删不掉"——直接拒。"""
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
    """删了还能加回来：同一个目录 = 同一个 id，墓碑被复用而不是长成第二个条目。"""
    project = tmp_path.parent / f"readd-{tmp_path.name}"
    project.mkdir()
    ws = client.post("/api/workspaces", json={"path": str(project)}).json()
    client.delete(f"/api/workspaces/{ws['id']}")

    again = client.post("/api/workspaces", json={"path": str(project)})

    assert again.status_code == 201, again.text
    assert again.json()["id"] == ws["id"]


def test_meta_declares_workspace_delete(client):
    """能力表是界面显示删除按钮的依据：老内核没有它，按钮就不该画出来。"""
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

    # full_access_ack 是完全访问的授予凭据（前端两步确认后才带它）。
    started = client.post(
        f"/api/sessions/{session['id']}/runs",
        json={"prompt": "问题", "auto_approve": True, "full_access_ack": True},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    # run_started 带上归属与权限：刷新页面后重建界面靠它（不能只存在内存里）。
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

    # 没有工作区默认权限可继承：不给 ack 就是 normal。
    stream = client.get(f"/api/runs/{run_id}/events").text
    assert '"permission": "normal"' in stream


def test_run_rejects_a_permission_field(client, tmp_path):
    """权限模式已删：请求里带 permission 一律 422（extra=forbid），值是什么都一样。"""
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

        # 未登记但可解析：显式指定它的 id 就能建会话，会话落在共享会话目录里它的那个子目录下。
        created = services.sessions.create(workspace=listed[0]["id"])
        assert created["workspace"]["id"] == listed[0]["id"]
        assert list(sessions_root(home).glob("*.jsonl"))
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


def test_a_permission_field_is_rejected_without_registering_the_workspace(client, tmp_path):
    """权限模式已删：注册载荷带 permission 在 schema 层就 422，不留"先落盘、再报错"的副作用。

    以前路由先 `require_new(path)` 写一次、再 `registry.add(..., permission=...)`
    写第二次：非法 permission 于是 500 + 工作区已登记 + 重试变 409（一次请求既没
    成功又留下了副作用）。
    """
    target = tmp_path.parent / f"invalid-{tmp_path.name}"
    target.mkdir()

    # 值是什么都一样：这个字段本身不存在（extra=forbid），manual/auto/full 一并被拒。
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


# ---------------- 会话归属的定位成本（P2-6） ----------------


def test_session_lookup_scans_once_then_hits_the_cache(sandbox, monkeypatch):
    """定位一个会话要扫"每个工作区 × 每个会话文件头"，而它每次运行启动都要付一次。

    扫一次就把所有会话记进缓存；TTL 内再查同一个会话不该再扫。
    """
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

        # 第一次：缓存里没有（上面 create 时登记过 second，先清掉模拟"新进程"）
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
