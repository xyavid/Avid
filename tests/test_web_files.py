"""工作区文件浏览端点的边界。

隔离测试先列失败清单，每条都有用例：
  ① 越出工作区（`..`）；② symlink 穿透；③ 凭据类路径（`.env` / `.pem` / `.ssh`）；
  ④ 未知工作区；⑤ 不是目录 / 不是文件；⑥ 超大文件截断；⑦ 二进制不猜编码；
  ⑧ 条目数截断。端点是只读的，所以没有写路径要测。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from avid.services import Services
from avid.services.files import MAX_ENTRIES, MAX_PREVIEW_BYTES
from avid.web import create_app


@pytest.fixture
def client(sandbox):
    services = Services(root=sandbox / ".avid" / "sessions")
    # 静态目录指向不存在的路径：断言不随环境里有没有构建产物变化。
    app = create_app(services=services, static_dir=sandbox / "static-not-built")
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client
    services.close()


@pytest.fixture
def ws(client, tmp_path) -> tuple[str, Path]:
    """登记一个临时工作区（目录 + 一个子目录 + 两个文件）。"""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("项目说明\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    created = client.post("/api/workspaces", json={"path": str(root)})
    assert created.status_code == 201, created.text
    return created.json()["id"], root


def code_of(response) -> str:
    return response.json()["error"]["code"]


def test_lists_directories_first_then_files(client, ws):
    workspace_id, _ = ws
    body = client.get(f"/api/workspaces/{workspace_id}/files").json()

    assert body["path"] == ""
    assert body["parent"] is None
    assert [entry["name"] for entry in body["entries"]] == ["src", "README.md"]
    assert [entry["kind"] for entry in body["entries"]] == ["dir", "file"]
    assert body["entries"][0]["size"] is None  # 目录不给大小
    assert body["entries"][1]["size"] == len("项目说明\n".encode())
    assert body["truncated"] is False


def test_walks_into_a_subdirectory_and_back(client, ws):
    workspace_id, _ = ws
    body = client.get(f"/api/workspaces/{workspace_id}/files", params={"path": "src"}).json()

    assert body["path"] == "src"
    assert body["parent"] == ""  # 上一级是根
    assert [entry["name"] for entry in body["entries"]] == ["main.py"]
    assert body["entries"][0]["path"] == "src/main.py"


def test_refuses_paths_outside_the_workspace(client, ws):
    workspace_id, _ = ws
    response = client.get(f"/api/workspaces/{workspace_id}/files", params={"path": "../.."})

    assert response.status_code == 403
    assert code_of(response) == "file_outside"


def test_refuses_a_symlink_that_escapes(client, ws, tmp_path):
    workspace_id, root = ws
    (tmp_path / "secret.txt").write_text("工作区外的内容", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")

    response = client.get(f"/api/workspaces/{workspace_id}/file", params={"path": "link.txt"})

    assert response.status_code == 403
    assert code_of(response) == "file_outside"


def test_refuses_credentials(client, ws):
    workspace_id, root = ws
    (root / ".env").write_text("API_KEY=1\n", encoding="utf-8")
    (root / "cert.pem").write_text("-----BEGIN\n", encoding="utf-8")
    (root / ".ssh").mkdir()
    (root / ".ssh" / "id_rsa").write_text("key\n", encoding="utf-8")

    for path in (".env", "cert.pem"):
        response = client.get(f"/api/workspaces/{workspace_id}/file", params={"path": path})
        assert response.status_code == 403, path
        assert code_of(response) == "file_sensitive", path

    # 列目录也一样：`.ssh` 是敏感组件，连它下面的名字都不给
    response = client.get(f"/api/workspaces/{workspace_id}/files", params={"path": ".ssh"})
    assert response.status_code == 403
    assert code_of(response) == "file_sensitive"


def test_unknown_workspace_is_not_found(client):
    response = client.get("/api/workspaces/w-nope/files")

    assert response.status_code == 404
    assert code_of(response) == "workspace_not_found"


def test_directory_and_file_kinds_are_checked(client, ws):
    workspace_id, _ = ws
    not_a_dir = client.get(f"/api/workspaces/{workspace_id}/files", params={"path": "README.md"})
    not_a_file = client.get(f"/api/workspaces/{workspace_id}/file", params={"path": "src"})

    assert not_a_dir.status_code == 400
    assert code_of(not_a_dir) == "file_not_directory"
    assert not_a_file.status_code == 404
    assert code_of(not_a_file) == "file_missing"


def test_preview_truncates_the_text(client, ws):
    workspace_id, root = ws
    (root / "big.txt").write_text("行\n" * 200_000, encoding="utf-8")

    body = client.get(f"/api/workspaces/{workspace_id}/file", params={"path": "big.txt"}).json()

    assert body["truncated"] is True
    assert len(body["text"]) <= MAX_PREVIEW_BYTES
    assert body["size"] > MAX_PREVIEW_BYTES
    assert body["binary"] is False


def test_binary_reports_the_fact_without_text(client, ws):
    workspace_id, root = ws
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")

    body = client.get(f"/api/workspaces/{workspace_id}/file", params={"path": "blob.bin"}).json()

    assert body["binary"] is True
    assert body["text"] is None


def test_entry_cap_truncates_the_listing(client, ws):
    workspace_id, root = ws
    many = root / "many"
    many.mkdir()
    for index in range(MAX_ENTRIES + 20):
        (many / f"f{index:04}.txt").write_text("x", encoding="utf-8")

    body = client.get(f"/api/workspaces/{workspace_id}/files", params={"path": "many"}).json()

    assert len(body["entries"]) == MAX_ENTRIES
    assert body["truncated"] is True
