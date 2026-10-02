"""BYOK 三层配置（Provider / Model / Binding）与 secret 引用解析。

契约要点：
- **配置只存引用**：`~/.avid/models.json` 里没有明文密钥；明文在 `~/.avid/secrets.json`
  （0600、原子写），配置里只有 `secret_ref` 指向它——「配置会被泄露」假设下的最小机制；
- **解析优先级**：本次运行覆盖（`providerId/modelId` ref，或命中绑定提供商的裸模型名）
  > chat 绑定；两者都不成立就是 ConfigError——BYOK 是模型连接的唯一来源，没有回落；
- **能力守卫**：chat 槽位（含按运行覆盖）的模型声明 `tool_calling=false` 时拒绝解析；
- **损坏降级分两档**：JSON 解析失败 → 警告 + 按「没有配置」处理（同样报错，不静默
  换到别的东西）；结构非法（协议未知、引用不存在…）→ ConfigError——文件可读但
  内容错时静默换端点比报错更危险；
- 协议枚举对齐 providers/ 注册表：openai-compatible 与 ollama 归 openai 族，
  anthropic、google 归各自族；oauth 不做（没有授权流基础设施）。
"""

from __future__ import annotations

import json
import stat

import pytest

from avid.ai.byok import (
    ByokConfig,
    ModelAuth,
    ModelCapabilities,
    ModelDecl,
    ProviderDecl,
    config_path,
    delete_secret,
    load_byok,
    resolve_chat,
    save_byok,
    secrets_path,
    set_secret,
)
from avid.ai.config import ConfigError


@pytest.fixture
def byok_env(tmp_path, monkeypatch):
    """BYOK 两个文件的路径都指到本用例的临时目录（盖掉 conftest 的种子配置）。"""
    cfg = tmp_path / "models.json"
    sec = tmp_path / "secrets.json"
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(cfg))
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(sec))
    return cfg, sec


def make_config(**overrides) -> ByokConfig:
    """一份最小合法配置：deepseek 一家一模型，chat 绑定指向它。"""
    provider = ProviderDecl(
        id="deepseek",
        label="DeepSeek",
        protocol="openai-compatible",
        base_url="https://api.deepseek.example/v1",
        auth=ModelAuth(type="bearer", secret_ref="deepseek"),
        models=(
            ModelDecl(
                id="deepseek-chat",
                context_window=65536,
                capabilities=ModelCapabilities(tool_calling=True),
            ),
        ),
    )
    fields = {
        "providers": {"deepseek": provider},
        "bindings": {"chat": "deepseek/deepseek-chat"},
    }
    fields.update(overrides)
    return ByokConfig(**fields)


def write_raw(path, raw) -> None:
    path.write_text(json.dumps(raw), encoding="utf-8")


# ---------------- 文件与 secret 存取 ----------------


def test_missing_file_is_a_config_error_with_repair_text(byok_env):
    assert load_byok() is None
    with pytest.raises(ConfigError, match="还没有模型配置"):
        resolve_chat()


def test_config_roundtrip(byok_env):
    cfg, _ = byok_env
    save_byok(make_config())

    loaded = load_byok()
    assert loaded is not None
    assert loaded.providers["deepseek"].base_url == "https://api.deepseek.example/v1"
    assert loaded.bindings["chat"] == "deepseek/deepseek-chat"
    assert config_path() == cfg


def test_corrupt_config_behaves_like_missing(byok_env):
    cfg, _ = byok_env
    cfg.write_text("{oops", encoding="utf-8")

    assert load_byok() is None
    with pytest.raises(ConfigError, match="还没有模型配置"):
        resolve_chat()


def test_invalid_structure_raises_instead_of_falling_back(byok_env):
    cfg, _ = byok_env
    write_raw(
        cfg,
        {
            "providers": [
                {
                    "id": "x",
                    "label": "X",
                    "protocol": "nope",
                    "base_url": "https://x.example/v1",
                }
            ],
            "bindings": {},
        },
    )

    with pytest.raises(ConfigError, match="protocol"):
        load_byok()


def test_secret_roundtrip_and_owner_only(byok_env):
    _, sec = byok_env
    set_secret("deepseek", "sk-abc")

    raw = json.loads(sec.read_text(encoding="utf-8"))
    assert raw == {"deepseek": "sk-abc"}
    assert stat.S_IMODE(sec.stat().st_mode) == 0o600

    delete_secret("deepseek")
    assert json.loads(sec.read_text(encoding="utf-8")) == {}
    assert secrets_path() == sec


# ---------------- 校验 ----------------


def test_rejects_bad_provider_id(byok_env):
    cfg, _ = byok_env
    write_raw(
        cfg,
        {
            "providers": [
                {
                    "id": "Deep_Seek",
                    "label": "x",
                    "protocol": "openai-compatible",
                    "base_url": "https://x.example/v1",
                    "auth": {"type": "bearer", "secret_ref": "x"},
                    "models": [{"id": "m"}],
                }
            ],
            "bindings": {"chat": None},
        },
    )
    with pytest.raises(ConfigError, match="id"):
        load_byok()


def test_rejects_binding_to_missing_model(byok_env):
    cfg, _ = byok_env
    write_raw(
        cfg,
        {
            "providers": [
                {
                    "id": "ds",
                    "label": "x",
                    "protocol": "openai-compatible",
                    "base_url": "https://x.example/v1",
                    "auth": {"type": "none"},
                    "models": [{"id": "m1"}],
                }
            ],
            "bindings": {"chat": "ds/m2"},
        },
    )
    with pytest.raises(ConfigError, match="ds/m2"):
        load_byok()


def test_rejects_chat_binding_without_tool_calling(byok_env):
    cfg, _ = byok_env
    write_raw(
        cfg,
        {
            "providers": [
                {
                    "id": "ds",
                    "label": "x",
                    "protocol": "openai-compatible",
                    "base_url": "https://x.example/v1",
                    "auth": {"type": "none"},
                    "models": [{"id": "m1", "capabilities": {"tool_calling": False}}],
                }
            ],
            "bindings": {"chat": "ds/m1"},
        },
    )
    with pytest.raises(ConfigError, match="工具调用"):
        load_byok()


# ---------------- 解析 ----------------


def test_chat_binding_resolves_to_config(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    config = resolve_chat()
    assert config.api_key == "sk-live"
    assert config.base_url == "https://api.deepseek.example/v1"
    assert config.model == "deepseek-chat"
    assert config.context_window == 65536
    assert config.provider == "openai"  # openai-compatible → openai 协议族


def test_binding_resolves_the_bound_model(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    # conftest 种的是 test/test-model；这份配置的 chat 绑定指向 deepseek/deepseek-chat
    config = resolve_chat()
    assert (config.api_key, config.model) == ("sk-live", "deepseek-chat")


def test_unbound_binding_is_a_config_error(byok_env):
    save_byok(make_config(bindings={"chat": None}))

    with pytest.raises(ConfigError, match="chat 槽位还没有绑定"):
        resolve_chat()


def test_ref_override_picks_any_provider(byok_env):
    set_secret("local", "")
    cfg = make_config(bindings={"chat": "deepseek/deepseek-chat"})
    cfg.providers["local"] = ProviderDecl(
        id="local",
        label="本地",
        protocol="ollama",
        base_url="http://127.0.0.1:11434/v1",
        auth=ModelAuth(type="none"),
        models=(ModelDecl(id="qwen:7b"),),
    )
    save_byok(cfg)

    config = resolve_chat(model="local/qwen:7b")
    assert (config.model, config.provider) == ("qwen:7b", "openai")
    assert config.api_key == ""  # none 鉴权：空 key


def test_bare_model_override_must_exist_in_bound_provider(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    # 绑定提供商里的裸模型名 → BYOK
    assert resolve_chat(model="deepseek-chat").base_url == "https://api.deepseek.example/v1"
    # 不在提供商目录里的裸名 → 报错（没有 env 回落；要用别家模型就写 ref 形式）
    with pytest.raises(ConfigError, match="deepseek"):
        resolve_chat(model="some-other-model")


def test_ref_override_to_unknown_provider_raises(byok_env):
    save_byok(make_config())

    with pytest.raises(ConfigError, match="ghost"):
        resolve_chat(model="ghost/m1")


def test_missing_secret_gives_repair_message(byok_env):
    save_byok(make_config())

    with pytest.raises(ConfigError, match="密钥"):
        resolve_chat()


def test_header_auth_and_extra_fields_flow_into_config(byok_env):
    set_secret("gw", "sk-gw")
    provider = ProviderDecl(
        id="gw",
        label="网关",
        protocol="openai-compatible",
        base_url="https://gw.example/v1",
        auth=ModelAuth(type="header", secret_ref="gw", header_name="X-Key"),
        headers={"X-Trace": "avid"},
        extra_body={"provider": {"order": ["b1"]}},
        models=(ModelDecl(id="m1", max_output=1024),),
    )
    save_byok(ByokConfig(providers={"gw": provider}, bindings={"chat": "gw/m1"}))

    config = resolve_chat()
    assert config.extra_headers == {"X-Key": "sk-gw", "X-Trace": "avid"}
    assert config.extra_body == {"provider": {"order": ["b1"]}}
    assert config.max_output == 1024
    assert config.context_window is None  # 未声明窗口 → 交给运行期的内置表兜底


def test_tool_calling_guard_applies_to_override_too(byok_env):
    provider = ProviderDecl(
        id="ds",
        label="x",
        protocol="openai-compatible",
        base_url="https://x.example/v1",
        auth=ModelAuth(type="none"),
        models=(ModelDecl(id="chat-only", capabilities=ModelCapabilities(tool_calling=False)),),
    )
    save_byok(ByokConfig(providers={"ds": provider}, bindings={"chat": None}))

    with pytest.raises(ConfigError, match="工具调用"):
        resolve_chat(model="ds/chat-only")
