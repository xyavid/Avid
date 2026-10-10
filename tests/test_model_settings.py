"""Line format and contracts of the BYOK settings endpoints (GET/PUT/DELETE/POST
/api/settings/byok): keys are write-only, so no response echoes api_key and GET reports a
per-provider key_set bool.

PUT saves providers plus chat bindings as a whole, stripping the payload's api_key into the
0600 ``~/.avid/secrets.json``; an invalid payload returns invalid_request and writes nothing.
"""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient
from support import collect  # noqa: F401  (shared collector for all web tests)

from avid.providers.byok import config_path, load_byok, read_secrets, resolve_chat, secrets_path
from avid.providers.config import ConfigError
from avid.services import Services
from avid.web import create_app


@pytest.fixture(autouse=True)
def byok_paths(tmp_path, monkeypatch):
    """Point at a per-file empty directory, overriding conftest's seeded config."""
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


# ---------------- GET: reading and the key boundary ----------------


def test_get_empty_state_has_no_legacy_block(tmp_path):
    http = client(tmp_path)

    body = http.get("/api/settings/byok").json()

    assert body["providers"] == []
    assert body["bindings"] == {"chat": None}
    # BYOK is the only source: no legacy block, the UI owns the empty state.
    assert "legacy" not in body


def test_get_hides_key_but_shows_key_set(tmp_path):
    http = client(tmp_path)
    http.put("/api/settings/byok", json={"providers": [provider_payload()], "bindings": {}})

    body = http.get("/api/settings/byok").json()

    assert "api_key" not in json.dumps(body)
    assert body["providers"][0]["key_set"] is False  # no key submitted yet


# ---------------- PUT: save, secret on disk, immediate effect ----------------


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
    assert "sk-ui" not in json.dumps(body)  # write-only

    # No plaintext or auth block in the config file; the 0600 secrets file holds it by provider id.
    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert "sk-ui" not in json.dumps(raw)
    assert "auth" not in json.dumps(raw)
    assert read_secrets() == {"deepseek": "sk-ui"}
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600

    # Effective immediately: the next resolve_chat returns what the UI just saved.
    config = resolve_chat()
    assert (config.model, config.base_url, config.api_key) == (
        "deepseek-chat",
        "https://api.deepseek.example/v1",
        "sk-ui",
    )
    # meta's model and BYOK candidates change together.
    meta = http.get("/api/meta").json()["capabilities"]
    assert meta["model"] == "deepseek-chat"
    assert meta["models"] == [
        {"ref": "deepseek/deepseek-chat", "label": "DeepSeek · deepseek-chat", "reasoning_efforts": []}
    ]


def test_put_keeps_model_reasoning_levels(tmp_path):
    """档位是模型自己声明的：存了就得落盘、回读也得带回来，否则输入区永远没有档位可挑。"""
    http = client(tmp_path)
    models = [
        {
            "id": "reasoner",
            "context_window": 65536,
            "reasoning_efforts": ["low", "medium", "high"],
            "capabilities": {"tool_calling": True},
        }
    ]

    body = http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload(models=models, api_key="sk-x")], "bindings": {"chat": None}},
    ).json()

    assert body["providers"][0]["models"][0]["reasoning_efforts"] == ["low", "medium", "high"]
    # 落盘那份（重开连接走 GET，等于界面刷新后的读取）
    assert http.get("/api/settings/byok").json()["providers"][0]["models"][0]["reasoning_efforts"] == [
        "low",
        "medium",
        "high",
    ]
    # 运行期认的也是它：解析出来的模型带着档位，meta 的候选也带着
    assert list(load_byok().providers["deepseek"].models[0].reasoning_efforts) == [
        "low",
        "medium",
        "high",
    ]
    assert http.get("/api/meta").json()["capabilities"]["models"][0]["reasoning_efforts"] == [
        "low",
        "medium",
        "high",
    ]


def test_put_without_key_keeps_existing_secret(tmp_path):
    http = client(tmp_path)
    http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload(api_key="sk-keep")], "bindings": {}},
    )

    # The second save omits api_key because the UI never touched the key input
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
    assert read_secrets() == {}  # no half-written secret


def test_put_rejects_unknown_binding_slot(tmp_path):
    http = client(tmp_path)

    res = http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload()], "bindings": {"vision": "deepseek/deepseek-chat"}},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


# ---------------- test endpoint: testable before saving ----------------


def test_test_endpoint_reports_both_steps(tmp_path, monkeypatch):
    from avid.providers import verify as verify_module
    from avid.providers.protocol import Turn, Usage

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
    from avid.providers import verify as verify_module
    from avid.providers.protocol import Turn, Usage

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


# ---------------- POST /models: the 获取模型 listing ----------------


def _fake_send(monkeypatch, response):
    """Replace the transport call so URL, auth header and parsing run for real."""
    from avid.providers import openai_compat

    seen: dict = {}

    def fake(http, method, url, *, headers, json_body=None, policy=None):
        seen.update(method=method, url=url, headers=headers)
        return response

    monkeypatch.setattr(openai_compat, "send", fake)
    return seen


def test_models_endpoint_lists_ids_with_the_chat_auth(tmp_path, monkeypatch):
    import httpx

    seen = _fake_send(
        monkeypatch,
        httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}),
    )
    http = client(tmp_path)

    body = http.post(
        "/api/settings/byok/models", json={"provider": provider_payload(api_key="sk-test")}
    ).json()

    assert body["ok"] is True
    assert body["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.deepseek.example/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"


def test_models_endpoint_reports_a_bad_path_in_plain_words(tmp_path, monkeypatch):
    import httpx

    _fake_send(monkeypatch, httpx.Response(404, text='{"error":"not found"}'))
    http = client(tmp_path)

    body = http.post(
        "/api/settings/byok/models", json={"provider": provider_payload(api_key="sk-test")}
    ).json()

    assert body["ok"] is False
    assert body["models"] == []
    assert "/v1" in body["detail"]


def test_models_endpoint_says_so_when_the_endpoint_offers_nothing(tmp_path, monkeypatch):
    import httpx

    _fake_send(monkeypatch, httpx.Response(200, json={"data": []}))
    http = client(tmp_path)

    body = http.post(
        "/api/settings/byok/models", json={"provider": provider_payload(api_key="sk-test")}
    ).json()

    assert body["ok"] is False
    assert "/models" in body["detail"]


def test_config_file_endpoint_shows_the_path_and_raw_json(tmp_path):
    """界面要能看到磁盘上那份 models.json：路径 + 原样内容（密钥本就不在这个文件里）。"""
    http = client(tmp_path)

    empty = http.get("/api/settings/byok/file").json()
    assert empty["path"].endswith("models.json")
    assert empty["exists"] is False
    assert empty["raw"] == ""

    http.put(
        "/api/settings/byok",
        json={"providers": [provider_payload(api_key="sk-x")], "bindings": {"chat": None}},
    )

    body = http.get("/api/settings/byok/file").json()
    assert body["exists"] is True
    assert "sk-x" not in body["raw"]
    parsed = json.loads(body["raw"])
    assert [p["id"] for p in parsed["providers"]] == ["deepseek"]


# ---------------- DELETE: reset and fall back ----------------


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


# ---------------- resolution priority (BYOK beats legacy) ----------------


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

    assert config.api_key == "sk-live"  # not conftest's baseline test-key
    assert config.provider == "openai"  # openai-compatible maps to the openai protocol family


def test_unbound_config_cannot_resolve(tmp_path):
    http = client(tmp_path)
    http.put("/api/settings/byok", json={"providers": [provider_payload()], "bindings": {}})

    with pytest.raises(ConfigError, match="chat 槽位还没有绑定"):
        resolve_chat()
