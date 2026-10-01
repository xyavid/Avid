"""界面模型配置（`~/.avid/model.toml` 叠加 .env）与 GET/PUT/DELETE /api/settings/model。

契约要点：
- 叠加优先级：界面配置文件 > 环境变量（界面是最近的显式动作；.env 是初始来源）；
- 密钥只入不出：任何响应都不返回 api_key，只给 api_key_set 布尔；
- 文件损坏降级为「没有文件」（警告 + 回落 .env），不阻断启动；
- 生效路径：load_config 每次运行都会调用，保存后对新消息立即生效，无需重启。
"""

from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient
from support import collect  # noqa: F401  (统一收集器，保持与其它 web 用例同构)

from avid.ai.config import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    ENV_PROVIDER,
    load_config,
    read_model_settings,
    write_model_settings,
)
from avid.svc import Services
from avid.web import create_app


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """把界面配置指向临时文件，并给 .env 侧一组基线值。"""
    path = tmp_path / "model.toml"
    monkeypatch.setenv("AVID_MODEL_CONFIG", str(path))
    monkeypatch.setenv(ENV_API_KEY, "env-key")
    monkeypatch.setenv(ENV_MODEL, "env-model")
    monkeypatch.setenv(ENV_BASE_URL, "https://env.example/v1")
    monkeypatch.delenv(ENV_PROVIDER, raising=False)
    return path


def client(tmp_path) -> TestClient:
    services = Services(root=tmp_path / ".avid" / "sessions")
    return TestClient(create_app(services=services), base_url="http://127.0.0.1:8765")


# ---------------- 叠加与文件 ----------------


def test_overlay_wins_over_env(settings_env):
    write_model_settings({"model": "file-model", "base_url": "https://file.example/v1"})

    config = load_config()

    assert config.model == "file-model"
    assert config.base_url == "https://file.example/v1"
    assert config.api_key == "env-key"  # 文件没写的字段回落 .env


def test_api_key_written_to_file_is_effective(settings_env):
    write_model_settings({"api_key": "file-key", "model": "m"})

    assert load_config().api_key == "file-key"


def test_file_roundtrip_merges_fields(settings_env):
    write_model_settings({"model": "m1"})
    write_model_settings({**read_model_settings(), "base_url": "https://b.example"})

    data = read_model_settings()
    assert data == {"model": "m1", "base_url": "https://b.example"}


def test_corrupt_file_degrades_to_env(settings_env):
    settings_env.write_text("{oops", encoding="utf-8")

    assert read_model_settings() == {}
    assert load_config().model == "env-model"


def test_written_file_is_owner_only(settings_env):
    write_model_settings({"api_key": "secret"})

    mode = stat.S_IMODE(settings_env.stat().st_mode)
    assert mode == 0o600, f"含密钥的配置文件权限应为 0600，实际 {oct(mode)}"


# ---------------- HTTP 端点 ----------------


def test_get_returns_effective_config_without_key(settings_env, tmp_path):
    write_model_settings({"model": "file-model"})
    http = client(tmp_path)

    body = http.get("/api/settings/model").json()

    assert body["model"] == "file-model"
    assert body["base_url"] == "https://env.example/v1"
    assert body["api_key_set"] is True
    assert body["overlay_active"] is True
    assert "api_key" not in body


def test_put_writes_overlay_and_takes_effect(settings_env, tmp_path):
    http = client(tmp_path)

    res = http.put(
        "/api/settings/model",
        json={"model": "ui-model", "base_url": "https://ui.example/v1", "api_key": "ui-key", "provider": "openai"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["model"] == "ui-model"
    assert body["api_key_set"] is True
    assert "api_key" not in body

    # 立即生效：下一次 load_config 就是界面值
    config = load_config()
    assert (config.model, config.base_url, config.api_key, config.provider) == (
        "ui-model",
        "https://ui.example/v1",
        "ui-key",
        "openai",
    )

    # 且 meta 的 model 同步变化（svc 每次查询都 load_config）
    assert http.get("/api/meta").json()["capabilities"]["model"] == "ui-model"


def test_put_empty_string_clears_field_back_to_env(settings_env, tmp_path):
    write_model_settings({"model": "file-model"})
    http = client(tmp_path)

    res = http.put("/api/settings/model", json={"model": ""})

    assert res.status_code == 200
    assert res.json()["model"] == "env-model"


def test_put_without_key_change_keeps_existing_key(settings_env, tmp_path):
    write_model_settings({"api_key": "kept-key"})
    http = client(tmp_path)

    res = http.put("/api/settings/model", json={"model": "m2"})

    assert res.status_code == 200
    assert res.json()["api_key_set"] is True
    assert load_config().api_key == "kept-key"


def test_put_rejects_unknown_provider(settings_env, tmp_path):
    http = client(tmp_path)

    res = http.put("/api/settings/model", json={"provider": "not-a-provider"})

    assert res.status_code == 422


def test_delete_resets_to_env(settings_env, tmp_path):
    write_model_settings({"model": "file-model", "api_key": "file-key"})
    http = client(tmp_path)

    res = http.delete("/api/settings/model")

    assert res.status_code == 204
    assert not settings_env.exists()
    assert load_config().model == "env-model"
    assert load_config().api_key == "env-key"
    assert http.get("/api/settings/model").json()["overlay_active"] is False


def test_get_degrades_without_any_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AVID_MODEL_CONFIG", str(tmp_path / "none.toml"))
    for name in (ENV_API_KEY, ENV_MODEL, ENV_BASE_URL, ENV_PROVIDER):
        monkeypatch.delenv(name, raising=False)
    http = client(tmp_path)

    body = http.get("/api/settings/model").json()

    assert body["model"] is None
    assert body["base_url"] is None
    assert body["api_key_set"] is False
