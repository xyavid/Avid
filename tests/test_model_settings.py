"""BYOK 设置端点（GET/PUT/DELETE+POST /api/settings/byok）与三层配置的线格式。

契约要点：
- 密钥只入不出：任何响应都不返回 api_key 明文，GET 只给每家的 key_set 布尔；
- PUT 是整体保存：providers 全量 + chat 绑定；载荷里的 api_key 剥出落
  `~/.avid/secrets.json`（0600，按 provider id 索引），配置文件里不存明文；
- 鉴权隐式：有密钥就按协议标准头发送，没存就不带——没有 auth_type 可选；
- validate 不过就不落盘（invalid_request 信封），也不会留下半份密钥；
- 测试端点针对**载荷**而不是已保存配置：保存前就能测，且不产生写密钥文件的副作用；
- 生效路径：resolve_chat 每次运行都重读文件，保存后对新消息立即生效，无需重启。
"""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient
from support import collect  # noqa: F401  (统一收集器，保持与其它 web 用例同构)

from avid.ai.byok import config_path, load_byok, read_secrets, resolve_chat, secrets_path
from avid.ai.config import ConfigError
from avid.svc import Services
from avid.web import create_app


@pytest.fixture(autouse=True)
def byok_paths(tmp_path, monkeypatch):
    """指向本文件专用的空目录（盖掉 conftest 的种子配置，从空状态测起）。"""
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(tmp_path / "settings-byok" / "models.json"))
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(tmp_path / "settings-byok" / "secrets.json"))


def provider_payload(**overrides) -> dict:
    base = {
        "id": "deepseek",
        "label": "DeepSeek",
        "protocol": "openai-compatible",
        "base_url": "https://api.deepseek.example/v1",
        "models": [
            {
                "id": "deepseek-chat",
                "context_window": 65536,
                "capabilities": {"tool_calling": True},
            }
        ],
    }
    base.update(overrides)
    return base


def client(tmp_path) -> TestClient:
    services = Services(root=tmp_path / ".avid" / "sessions")
    return TestClient(create_app(services=services), base_url="http://127.0.0.1:8765")


# ---------------- GET：读与密钥边界 ----------------


def test_get_empty_state_has_no_legacy_block(tmp_path):
    http = client(tmp_path)

    body = http.get("/api/settings/byok").json()

    assert body["providers"] == []
    assert body["bindings"] == {"chat": None}
    # BYOK 是唯一来源：没有 legacy 块，空态由界面自己引导
    assert "legacy" not in body


def test_get_hides_key_but_shows_key_set(tmp_path):
    http = client(tmp_path)
    http.put("/api/settings/byok", json={"providers": [provider_payload()], "bindings": {}})

    body = http.get("/api/settings/byok").json()

    assert "api_key" not in json.dumps(body)
    assert body["providers"][0]["key_set"] is False  # 还没填过密钥


# ---------------- PUT：保存、密钥落盘、立即生效 ----------------


def test_put_writes_config_and_secret_and_takes_effect(tmp_path):
    http = client(tmp_path)

    res = http.put(
        "/api/settings/byok",
        json={
            "providers": [provider_payload(api_key="sk-ui")],
            "bindings": {"chat": "deepseek/deepseek-chat"},
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["providers"][0]["key_set"] is True
    assert "sk-ui" not in json.dumps(body)  # 只入不出

    # 配置文件不存明文、也不再有 auth 块；明文在 0600 的密钥文件里，按 provider id 索引
    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert "sk-ui" not in json.dumps(raw)
    assert "auth" not in json.dumps(raw)
    assert read_secrets() == {"deepseek": "sk-ui"}
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600

    # 立即生效：下一次 resolve_chat 就是界面保存的这份
    config = resolve_chat()
    assert (config.model, config.base_url, config.api_key) == (
        "deepseek-chat",
        "https://api.deepseek.example/v1",
        "sk-ui",
    )
    # meta 的 model 与 BYOK 候选同步变化
    meta = http.get("/api/meta").json()["capabilities"]
    assert meta["model"] == "deepseek-chat"
    assert meta["models"] == [{"ref": "deepseek/deepseek-chat", "label": "DeepSeek · deepseek-chat"}]


def test_put_without_key_keeps_existing_secret(tmp_path):
    http = client(tmp_path)
    http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload(api_key="sk-keep")], "bindings": {}},
    )

    # 第二次保存不带 api_key 字段（界面没动密钥输入框）
    http.put("/api/settings/byok", json={"providers": [provider_payload()], "bindings": {}})

    assert read_secrets() == {"deepseek": "sk-keep"}


def test_put_empty_key_clears_secret(tmp_path):
    http = client(tmp_path)
    http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload(api_key="sk-gone")], "bindings": {}},
    )

    http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload(api_key="")], "bindings": {}},
    )

    assert read_secrets() == {}


def test_put_rejects_invalid_config_without_writing(tmp_path):
    http = client(tmp_path)

    res = http.put(
        "/api/settings/byok",
        json={
            "providers": [
                provider_payload(
                    id="ds",
                    models=[
                        {"id": "m1", "capabilities": {"tool_calling": False}}
                    ],
                )
            ],
            "bindings": {"chat": "ds/m1"},
        },
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"
    assert not config_path().exists()
    assert read_secrets() == {}  # 没有留下半份密钥


def test_put_rejects_unknown_binding_slot(tmp_path):
    http = client(tmp_path)

    res = http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload()], "bindings": {"vision": "deepseek/deepseek-chat"}},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


# ---------------- 测试端点：保存前就能测 ----------------


def test_test_endpoint_reports_both_steps(tmp_path, monkeypatch):
    from avid.ai import verify as verify_module
    from avid.ai.protocol import Turn, Usage

    def fake(config, messages, *, system=None, tools=None, max_tokens=None, client=None):
        return Turn(
            message={"role": "assistant", "content": "ok"},
            text="ok",
            tool_calls=[{"id": "t1"}] if tools else [],
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="deepseek-chat",
            finish_reason="stop",
        )

    monkeypatch.setattr(verify_module, "chat_completion", fake)
    http = client(tmp_path)

    res = http.post(
        "/api/settings/byok/test",
        json={"provider": provider_payload(api_key="sk-test"), "model_id": "deepseek-chat"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert [s["step"] for s in body["steps"]] == ["chat", "tool"]


def test_test_endpoint_reports_smoke_failure(tmp_path, monkeypatch):
    from avid.ai import verify as verify_module
    from avid.ai.protocol import Turn, Usage

    def fake(config, messages, *, system=None, tools=None, max_tokens=None, client=None):
        return Turn(
            message={"role": "assistant", "content": "好的"},
            text="好的",
            tool_calls=[],
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="deepseek-chat",
            finish_reason="stop",
        )

    monkeypatch.setattr(verify_module, "chat_completion", fake)
    http = client(tmp_path)

    body = http.post(
        "/api/settings/byok/test",
        json={"provider": provider_payload(api_key="sk-test"), "model_id": "deepseek-chat"},
    ).json()

    assert body["ok"] is False
    assert body["steps"][1]["ok"] is False
    assert "工具" in body["steps"][1]["detail"]


# ---------------- DELETE：重置回落 ----------------


def test_delete_drops_both_files_and_falls_back(tmp_path):
    http = client(tmp_path)
    http.put(
        "/api/settings/byok",
        json={
            "providers": [provider_payload(api_key="sk-bye")],
            "bindings": {"chat": "deepseek/deepseek-chat"},
        },
    )
    assert config_path().exists()

    res = http.delete("/api/settings/byok")

    assert res.status_code == 204
    assert not config_path().exists()
    assert not secrets_path().exists()
    with pytest.raises(ConfigError, match="还没有模型配置"):
        resolve_chat()
    assert load_byok() is None


# ---------------- 解析优先级（BYOK 胜过 legacy） ----------------


def test_byok_binding_beats_legacy_env(tmp_path):
    http = client(tmp_path)
    http.put(
        "/api/settings/byok",
        json={
            "providers": [provider_payload(api_key="sk-live")],
            "bindings": {"chat": "deepseek/deepseek-chat"},
        },
    )

    config = resolve_chat()

    assert config.api_key == "sk-live"  # 不是 conftest 基线的 test-key
    assert config.provider == "openai"  # openai-compatible → openai 协议族


def test_unbound_config_cannot_resolve(tmp_path):
    http = client(tmp_path)
    http.put("/api/settings/byok", json={"providers": [provider_payload()], "bindings": {}})

    with pytest.raises(ConfigError, match="chat 槽位还没有绑定"):
        resolve_chat()
