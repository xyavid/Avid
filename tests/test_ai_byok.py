"""BYOK config (provider / model / binding) and key resolution.

Config holds references only: plaintext keys live in ``secrets.json`` (0600, keyed by provider
id); a run override beats the chat binding, and BYOK is the only source (no fallback). Corrupt
JSON degrades to "no config" with a warning, while structurally invalid config raises
``ConfigError`` instead of silently switching endpoints.
"""

from __future__ import annotations

import json
import stat

import pytest

from avid.providers.byok import (
    ByokConfig,
    ModelCapabilities,
    ModelDecl,
    ProviderDecl,
    byok_model_candidates,
    config_path,
    delete_secret,
    load_byok,
    resolve_chat,
    save_byok,
    secrets_path,
    set_secret,
    validate_byok,
)
from avid.providers.config import ConfigError


@pytest.fixture
def byok_env(tmp_path, monkeypatch):
    """Point both BYOK file paths at this test's tmp dir (overriding conftest seeds)."""
    cfg = tmp_path / "models.json"
    sec = tmp_path / "secrets.json"
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(cfg))
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(sec))
    return cfg, sec


def make_config(**overrides) -> ByokConfig:
    """A minimal valid config: one deepseek provider/model with the chat binding pointing at it."""
    provider = ProviderDecl(
        id="deepseek",
        label="DeepSeek",
        protocol="openai-compatible",
        base_url="https://api.deepseek.example/v1",
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


# ---------------- files and secret storage ----------------


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


# ---------------- validation ----------------


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


# ---------------- resolution ----------------


def test_vision_capability_rides_into_the_config(byok_env):
    """Vision is a declaration like any other capability: it rides into Config at resolve time,
    and the caller enforces it per message."""
    _, sec = byok_env
    sec.write_text(json.dumps({"blind": "sk"}), encoding="utf-8")
    raw = {
        "providers": [
            {
                "id": "blind",
                "label": "x",
                "protocol": "openai-compatible",
                "base_url": "https://x.example/v1",
                "models": [
                    {"id": "no-image", "capabilities": {"vision": False}},
                    {"id": "maybe", "capabilities": {}},
                ],
            }
        ],
        "bindings": {"chat": "blind/no-image"},
    }
    cfg = byok_env[0]
    write_raw(cfg, raw)

    assert resolve_chat().vision is False
    assert resolve_chat(model="blind/maybe").vision is None


def test_chat_binding_resolves_to_config(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    config = resolve_chat()
    assert config.api_key == "sk-live"
    assert config.base_url == "https://api.deepseek.example/v1"
    assert config.model == "deepseek-chat"
    assert config.context_window == 65536
    assert config.provider == "openai"  # openai-compatible -> openai protocol family


def test_binding_resolves_the_bound_model(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    # conftest seeds test/test-model; this config's chat binding is deepseek/deepseek-chat
    config = resolve_chat()
    assert (config.api_key, config.model) == ("sk-live", "deepseek-chat")


def test_unbound_binding_is_a_config_error(byok_env):
    save_byok(make_config(bindings={"chat": None}))

    with pytest.raises(ConfigError, match="chat 槽位还没有绑定"):
        resolve_chat()


def test_ref_override_picks_any_provider(byok_env):
    cfg = make_config(bindings={"chat": "deepseek/deepseek-chat"})
    cfg.providers["local"] = ProviderDecl(
        id="local",
        label="本地",
        protocol="ollama",
        base_url="http://127.0.0.1:11434/v1",
        models=(ModelDecl(id="qwen:7b"),),
    )
    save_byok(cfg)

    config = resolve_chat(model="local/qwen:7b")
    assert (config.model, config.provider) == ("qwen:7b", "openai")
    assert config.api_key == ""  # no local key -> no auth header (local service)


def test_bare_model_override_must_exist_in_bound_provider(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    # Bare model name in the bound provider -> BYOK
    assert resolve_chat(model="deepseek-chat").base_url == "https://api.deepseek.example/v1"
    # Bare name outside the provider catalog -> error (no env fallback; use the ref form)
    with pytest.raises(ConfigError, match="deepseek"):
        resolve_chat(model="some-other-model")


def test_ref_override_to_unknown_provider_raises(byok_env):
    save_byok(make_config())

    with pytest.raises(ConfigError, match="ghost"):
        resolve_chat(model="ghost/m1")


def test_absent_secret_means_no_auth_not_an_error(byok_env):
    """A missing key is not an error: api_key is empty, so no auth header is sent."""
    save_byok(make_config())

    config = resolve_chat()
    assert config.api_key == ""
    assert config.model == "deepseek-chat"


def test_key_from_secrets_by_provider_id(byok_env):
    set_secret("deepseek", "sk-live")
    save_byok(make_config())

    assert resolve_chat().api_key == "sk-live"


def test_legacy_auth_block_is_ignored(byok_env):
    """A legacy ``auth`` block (bearer/header/none) is silently ignored at load time."""
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
                    "auth": {"type": "header", "secret_ref": "gw", "header_name": "X-Key"},
                    "models": [{"id": "m1"}],
                }
            ],
            "bindings": {"chat": "ds/m1"},
        },
    )

    loaded = load_byok()
    assert loaded is not None
    assert resolve_chat().api_key == ""  # key lookup is by provider id (ds), not secret_ref


def test_static_headers_and_extra_fields_flow_into_config(byok_env):
    set_secret("gw", "sk-gw")
    provider = ProviderDecl(
        id="gw",
        label="网关",
        protocol="openai-compatible",
        base_url="https://gw.example/v1",
        headers={"X-Trace": "avid"},
        extra_body={"provider": {"order": ["b1"]}},
        models=(ModelDecl(id="m1", max_output=1024),),
    )
    save_byok(ByokConfig(providers={"gw": provider}, bindings={"chat": "gw/m1"}))

    config = resolve_chat()
    # Static headers pass through; the key uses the protocol standard header, not a custom one
    assert config.extra_headers == {"X-Trace": "avid"}
    assert config.api_key == "sk-gw"
    assert config.extra_body == {"provider": {"order": ["b1"]}}
    assert config.max_output == 1024
    assert config.context_window is None  # undeclared window -> the built-in runtime table


def test_reasoning_effort_is_chosen_per_run_from_the_declared_list(byok_env):
    """Effort is declared by the model and chosen per run; the pick rides in Config."""
    provider = ProviderDecl(
        id="gw",
        label="网关",
        protocol="openai-compatible",
        base_url="https://gw.example/v1",
        models=(
            ModelDecl(id="deep", reasoning_efforts=("low", "medium", "high", "max")),
            ModelDecl(id="quick"),
        ),
    )
    save_byok(ByokConfig(providers={"gw": provider}, bindings={"chat": "gw/deep"}))

    assert resolve_chat("gw/deep", effort="max").reasoning_effort == "max"
    # No pick, no parameter (there is no implicit default effort)
    assert resolve_chat("gw/deep").reasoning_effort is None
    # The bound model takes the same path (bare-name resolution)
    assert resolve_chat(effort="low").reasoning_effort == "low"

    with pytest.raises(ConfigError, match="没有声明推理强度"):
        resolve_chat("gw/deep", effort="turbo")
    with pytest.raises(ConfigError, match="没有声明"):
        resolve_chat("gw/quick", effort="high")


def test_reasoning_effort_list_is_free_form_and_validated(byok_env):
    """Effort names are free-form; only empty, over-32-char, and duplicate entries fail."""
    ok = ModelDecl(id="m", reasoning_efforts=("minimal", "low", "xhigh"))
    validate_byok(
        ByokConfig(
            providers={
                "gw": ProviderDecl(
                    id="gw", label="x", protocol="openai-compatible",
                    base_url="https://gw.example/v1", models=(ok,),
                )
            },
            bindings={"chat": "gw/m"},
        )
    )

    for bad in (("",), ("a" * 33,), ("low", "low")):
        provider = ProviderDecl(
            id="gw", label="x", protocol="openai-compatible",
            base_url="https://gw.example/v1", models=(ModelDecl(id="m", reasoning_efforts=bad),),
        )
        with pytest.raises(ConfigError, match="reasoning_efforts"):
            validate_byok(ByokConfig(providers={"gw": provider}, bindings={"chat": "gw/m"}))


def test_reasoning_effort_list_survives_a_save_and_load_round_trip(byok_env):
    provider = ProviderDecl(
        id="gw",
        label="网关",
        protocol="openai-compatible",
        base_url="https://gw.example/v1",
        models=(
            ModelDecl(
                id="m1",
                reasoning_efforts=("low", "high", "max"),
                capabilities=ModelCapabilities(vision=True),
            ),
        ),
    )

    save_byok(ByokConfig(providers={"gw": provider}, bindings={"chat": "gw/m1"}))
    loaded = load_byok()

    assert loaded is not None
    model = loaded.providers["gw"].model("m1")
    assert model is not None
    assert model.reasoning_efforts == ("low", "high", "max")
    assert model.capabilities.vision is True

    # The list ships with model candidates for the composer's effort picker
    candidates = byok_model_candidates()
    assert candidates[0]["reasoning_efforts"] == ["low", "high", "max"]


def test_tool_calling_guard_applies_to_override_too(byok_env):
    provider = ProviderDecl(
        id="ds",
        label="x",
        protocol="openai-compatible",
        base_url="https://x.example/v1",
        models=(ModelDecl(id="chat-only", capabilities=ModelCapabilities(tool_calling=False)),),
    )
    save_byok(ByokConfig(providers={"ds": provider}, bindings={"chat": None}))

    with pytest.raises(ConfigError, match="工具调用"):
        resolve_chat(model="ds/chat-only")
