"""系统文件夹选择器：后端探测、取消与失败的区分、以及"新增工作区"这条 HTTP 路径。

真实对话框在测试里不能弹（会卡住等人），所以两个层次分开测：

* **后端逻辑**：注入 ``AVID_PICKER_CMD``（命令把选中路径打到 stdout）或替换 ``BACKENDS``；
* **HTTP 路径**：monkeypatch ``svc.workspaces.pick_directory``，只验协议与错误码。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, create_session, make_turn

from avid.svc import Services
from avid.svc import picker as picker_module
from avid.svc.picker import PickerFailed, PickerUnavailable, pick_directory
from avid.web import create_app
from avid.workspaces import WorkspaceRegistry

# ---------------- 后端 ----------------

def test_override_backend_returns_the_printed_path():
    chosen = pick_directory(env={"AVID_PICKER_CMD": "printf %s /tmp/chosen"})

    assert chosen == "/tmp/chosen"


def test_empty_output_means_cancelled():
    assert pick_directory(env={"AVID_PICKER_CMD": "true"}) is None


def test_nonzero_exit_means_cancelled_not_failed():
    """取消与失败必须分得开：zenity/kdialog 都用 1 表示取消。"""
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
    """起不来的后端只记一笔，继续试下一个——UI 不该因为 tkinter 拉不起来就失去这个功能。"""
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
    picker_module.available_backend.cache_clear()
    monkeypatch.setenv("AVID_PICKER_CMD", "printf %s /tmp/x")
    try:
        assert picker_module.available_backend() == "override"
    finally:
        picker_module.available_backend.cache_clear()


def test_available_backend_is_none_when_nothing_is_left(monkeypatch):
    picker_module.available_backend.cache_clear()
    monkeypatch.setattr(picker_module, "BACKENDS", ())
    try:
        assert picker_module.available_backend() is None
    finally:
        picker_module.available_backend.cache_clear()


# ---------------- HTTP ----------------

@pytest.fixture
def bundle(tmp_path):
    """一个不会碰真实家目录的服务 + 客户端。选择器后端按需替换。"""
    home = tmp_path.parent / f"picker-home-{tmp_path.name}"
    home.mkdir()
    services = Services(
        workspace_root=home,
        registry=WorkspaceRegistry(tmp_path / "registry.json"),
        chat=ScriptedChat([make_turn("答")]),
    )
    client = TestClient(
        create_app(services=services, static_dir=tmp_path / "unbuilt")
    )
    yield client, services, tmp_path
    services.close()


@pytest.fixture
def picked(tmp_path, monkeypatch):
    """把选择器替换成"返回指定路径"，避免测试里弹出真对话框。"""
    chosen = tmp_path.parent / f"picked-{tmp_path.name}"
    chosen.mkdir()

    def fake() -> str | None:
        return str(chosen)

    monkeypatch.setattr("avid.svc.workspaces.pick_directory", fake)
    return chosen


def test_pick_returns_the_chosen_folder(bundle, picked):
    client, _, _ = bundle

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 200
    assert response.json() == {"path": str(picked)}


def test_pick_cancel_changes_nothing(bundle, monkeypatch):
    client, services, _ = bundle
    before = services.workspaces.list()
    monkeypatch.setattr("avid.svc.workspaces.pick_directory", lambda: None)

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 200
    assert response.json() == {"path": None}
    assert services.workspaces.list() == before  # 取消 = 什么都没发生


def test_pick_without_a_backend_is_503_with_the_manual_way_out(bundle, monkeypatch):
    client, _, _ = bundle

    def unavailable():
        raise PickerUnavailable("这台机器上没有可用的系统文件夹选择器。")

    monkeypatch.setattr("avid.svc.workspaces.pick_directory", unavailable)

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "picker_unavailable"


def test_pick_while_one_is_open_is_409(bundle, picked):
    client, services, _ = bundle

    with services.workspaces._pick_lock:  # 模拟"已经有一个对话框开着"
        response = client.post("/api/workspaces/pick")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "picker_busy"


def test_picked_path_that_vanished_is_rejected(bundle, monkeypatch, tmp_path):
    client, _, _ = bundle
    monkeypatch.setattr(
        "avid.svc.workspaces.pick_directory", lambda: str(tmp_path / "vanished")
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
    # 持久化：写进了注册表文件，重新读一份注册表也看得见。
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
    # 没有重复添加：列表里只有一个它。
    ids = [item["id"] for item in client.get("/api/workspaces").json()["workspaces"]]
    assert ids.count(first["id"]) == 1


def test_adding_the_process_bound_folder_is_409_too(bundle):
    """绑定值也在候选列表里，所以它同样算"已有"，不该被登记成第二条。"""
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


# ---------------- 与"切换"的配合 ----------------

def test_new_workspace_is_usable_for_a_session_right_away(bundle, picked):
    """界面上的"切到新工作区"= 选中它；服务端这边的保证是**立刻可用来建会话**。"""
    client, _, _ = bundle
    created = client.post("/api/workspaces", json={"path": str(picked)}).json()

    session = create_session(client, workspace=created["id"])

    assert session.status_code == 201, session.text
    assert session.json()["workspace"]["id"] == created["id"]
    assert list((picked / ".avid" / "sessions").glob("*.jsonl"))


def test_meta_exposes_the_picker_backend(bundle, monkeypatch):
    """诊断字段必须真的出现在 /api/meta 里。

    响应 DTO 是 pydantic 模型：svc 里给了键、schema 里没声明，就会被**静默丢掉**，
    于是"这台机器其实有选择器"会显示成没有（点按钮没反应时人就查错了地方）。
    """
    client, _, _ = bundle
    monkeypatch.setattr("avid.svc.available_backend", lambda: "tkinter")

    capabilities = client.get("/api/meta").json()["capabilities"]

    assert capabilities["workspace_picker"] == "tkinter"
