"""BYOK 模型配置：三层（Provider / Model / Binding）与 secret 引用解析。

设计（参照 BYOK 配置规格，两处刻意偏离）：
- 文件键名用 snake_case——Avid 全仓约定，文件是私有格式，没有外部互操作，
  不值得为规格里的 camelCase 多一层名字映射；
- auth 只做 bearer / header / none——oauth 需要授权流基础设施，query 型自定义鉴权
  还没有真实需求，出现时再加。

两份文件（默认 `~/.avid/`，可用环境变量指到别处）：
- `models.json`：providers + bindings，**只存引用不存明文**——按「配置会被泄露」
  的假设设计，这份文件可以随意备份、分享；
- `secrets.json`：secret_ref → 明文密钥，0600、临时文件 + os.replace 原子写。

解析入口只有一个：`resolve_chat()`。优先级为本次运行覆盖（`providerId/modelId`
ref，或命中绑定提供商的裸模型名）> chat 绑定 > legacy（旧 model.toml 覆盖层 →
环境变量，即 `ai/config.load_config`）。损坏降级分两档：JSON 解析失败按「没有
配置」回落 legacy 并警告；结构非法（协议未知、引用不存在……）抛 ConfigError——
文件可读但内容错时，静默换端点比报错更危险。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    Config,
    ConfigError,
    _SETTINGS_TO_ENV,
    _max_parallel_tool_calls,
    load_config,
    read_model_settings,
    window_for,
)

logger = logging.getLogger("avid.ai.byok")

ENV_BYOK_CONFIG = "AVID_BYOK_CONFIG"
ENV_BYOK_SECRETS = "AVID_BYOK_SECRETS"

PROTOCOL_OPENAI_COMPATIBLE = "openai-compatible"
PROTOCOL_OLLAMA = "ollama"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_GOOGLE = "google"
#: 协议枚举；映射到 providers/ 注册表的三个协议族（ollama 走它的 /v1 兼容端点）。
PROTOCOLS = (PROTOCOL_OPENAI_COMPATIBLE, PROTOCOL_ANTHROPIC, PROTOCOL_GOOGLE, PROTOCOL_OLLAMA)
PROTOCOL_FAMILY = {
    PROTOCOL_OPENAI_COMPATIBLE: "openai",
    PROTOCOL_OLLAMA: "openai",
    PROTOCOL_ANTHROPIC: "anthropic",
    PROTOCOL_GOOGLE: "gemini",
}
DEFAULT_PROTOCOL_BASE_URLS = {
    PROTOCOL_OPENAI_COMPATIBLE: "https://api.openai.com/v1",
    PROTOCOL_OLLAMA: "http://localhost:11434/v1",
    PROTOCOL_ANTHROPIC: "https://api.anthropic.com",
    PROTOCOL_GOOGLE: "https://generativelanguage.googleapis.com/v1beta",
}

AUTH_BEARER = "bearer"
AUTH_HEADER = "header"
AUTH_NONE = "none"
AUTH_TYPES = (AUTH_BEARER, AUTH_HEADER, AUTH_NONE)

#: 唯一的角色槽位。binding 值形如 "providerId/modelId"，null = 未绑定（回落 legacy）。
CHAT_SLOT = "chat"
BINDING_SLOTS = (CHAT_SLOT,)

_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
_REF_PATTERN = re.compile(r"^([a-z0-9-]+)/(.+)$")

CAPABILITY_FIELDS = ("tool_calling", "vision", "json_mode", "streaming", "reasoning")


def config_path() -> Path:
    raw = os.environ.get(ENV_BYOK_CONFIG, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".avid" / "models.json"


def secrets_path() -> Path:
    raw = os.environ.get(ENV_BYOK_SECRETS, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".avid" / "secrets.json"


# ---------------- 声明模型（内存态） ----------------


@dataclass(frozen=True)
class ModelAuth:
    type: str = AUTH_BEARER
    secret_ref: str | None = None
    header_name: str | None = None


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool | None = None
    vision: bool | None = None
    json_mode: bool | None = None
    streaming: bool | None = None
    reasoning: bool | None = None


@dataclass(frozen=True)
class ModelDecl:
    id: str
    label: str | None = None
    context_window: int | None = None
    max_output: int | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)


@dataclass(frozen=True)
class ProviderDecl:
    id: str
    label: str
    protocol: str
    base_url: str
    auth: ModelAuth = field(default_factory=ModelAuth)
    headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    models: tuple[ModelDecl, ...] = ()

    def model(self, model_id: str) -> ModelDecl | None:
        for item in self.models:
            if item.id == model_id:
                return item
        return None


@dataclass(frozen=True)
class ByokConfig:
    providers: dict[str, ProviderDecl]
    bindings: dict[str, str | None]


# ---------------- 校验 ----------------


def _err(message: str) -> ConfigError:
    return ConfigError(message)


def _validate_provider(provider: ProviderDecl) -> None:
    if not _ID_PATTERN.match(provider.id):
        raise _err(f"provider id 只能用小写字母、数字与连字符：{provider.id!r}")
    if not provider.label.strip():
        raise _err(f"provider {provider.id} 缺少展示名（label）")
    if provider.protocol not in PROTOCOLS:
        raise _err(
            f"provider {provider.id} 的 protocol 必须是 {'、'.join(PROTOCOLS)} 之一："
            f"{provider.protocol!r}"
        )
    base = provider.base_url.strip()
    if not base.startswith(("http://", "https://")):
        raise _err(
            f"provider {provider.id} 的 base_url 必须以 http(s):// 开头：{base!r}\n"
            "OpenAI 兼容端点通常要以 /v1 结尾"
        )
    if provider.auth.type not in AUTH_TYPES:
        raise _err(
            f"provider {provider.id} 的 auth.type 必须是 {'、'.join(AUTH_TYPES)} 之一："
            f"{provider.auth.type!r}"
        )
    if provider.auth.type in (AUTH_BEARER, AUTH_HEADER):
        if not provider.auth.secret_ref or not _ID_PATTERN.match(provider.auth.secret_ref):
            raise _err(
                f"provider {provider.id} 的 auth.secret_ref 只能用小写字母、数字与连字符："
                f"{provider.auth.secret_ref!r}"
            )
    if provider.auth.type == AUTH_HEADER and not (provider.auth.header_name or "").strip():
        raise _err(f"provider {provider.id} 用 header 鉴权时必须给 header_name")
    if provider.models and len({m.id for m in provider.models}) != len(provider.models):
        raise _err(f"provider {provider.id} 有重复的模型 id")
    for model in provider.models:
        if not model.id.strip():
            raise _err(f"provider {provider.id} 有模型缺 id")
        for name in ("context_window", "max_output"):
            value = getattr(model, name)
            if value is not None and value < 1:
                raise _err(f"provider {provider.id} 模型 {model.id} 的 {name} 必须 ≥ 1：{value}")


def validate_byok(config: ByokConfig) -> None:
    """Structural validation; raises ConfigError with a repair-oriented message."""
    seen: set[str] = set()
    for provider in config.providers.values():
        if provider.id in seen:
            raise _err(f"provider id 重复：{provider.id}")
        seen.add(provider.id)
        _validate_provider(provider)

    for slot, binding in config.bindings.items():
        if slot not in BINDING_SLOTS:
            raise _err(f"未知的绑定槽位 {slot!r}；当前只支持：{'、'.join(BINDING_SLOTS)}")
        if binding is None:
            continue
        match = _REF_PATTERN.match(binding)
        if match is None:
            raise _err(f"绑定 {slot} 的值必须是 providerId/modelId 形式：{binding!r}")
        pid, mid = match.group(1), match.group(2)
        provider = config.providers.get(pid)
        if provider is None:
            raise _err(f"绑定 {slot} 指向不存在的提供商：{pid}")
        if not provider.enabled:
            raise _err(f"绑定 {slot} 指向已停用的提供商：{pid}")
        model = provider.model(mid)
        if model is None:
            raise _err(f"绑定 {slot} 指向 {pid} 下不存在的模型：{binding}")
        if model.capabilities.tool_calling is False:
            raise _err(
                f"绑定 {slot} 的模型 {binding} 声明不支持工具调用；"
                "chat 槽位需要能调工具的模型，否则「能聊天、一干活就废」"
            )


# ---------------- 文件 I/O ----------------


def _write_json_atomic(path: Path, payload: Mapping[str, Any], *, owner_only: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".byok-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if owner_only:
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


def _decl_to_json(provider: ProviderDecl) -> dict[str, Any]:
    return {
        "id": provider.id,
        "label": provider.label,
        "protocol": provider.protocol,
        "base_url": provider.base_url,
        "auth": {
            "type": provider.auth.type,
            "secret_ref": provider.auth.secret_ref,
            "header_name": provider.auth.header_name,
        },
        "headers": provider.headers,
        "extra_body": provider.extra_body,
        "enabled": provider.enabled,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "context_window": m.context_window,
                "max_output": m.max_output,
                "capabilities": {name: getattr(m.capabilities, name) for name in CAPABILITY_FIELDS},
            }
            for m in provider.models
        ],
    }


def save_byok(config: ByokConfig) -> Path:
    """Validate then persist the config (secrets live in their own file, never here)."""
    validate_byok(config)
    payload = {
        "providers": [_decl_to_json(p) for p in config.providers.values()],
        "bindings": {slot: config.bindings.get(slot) for slot in BINDING_SLOTS},
    }
    return _write_json_atomic(config_path(), payload, owner_only=False)


def _parse_capabilities(raw: Any, where: str) -> ModelCapabilities:
    if raw is None:
        return ModelCapabilities()
    if not isinstance(raw, dict):
        raise _err(f"{where} 的 capabilities 必须是对象")
    unknown = set(raw) - set(CAPABILITY_FIELDS)
    if unknown:
        raise _err(f"{where} 的 capabilities 有未知字段：{'、'.join(sorted(unknown))}")
    values = {}
    for name in CAPABILITY_FIELDS:
        value = raw.get(name)
        if value is not None and not isinstance(value, bool):
            raise _err(f"{where} 的 capabilities.{name} 必须是布尔值：{value!r}")
        values[name] = value
    return ModelCapabilities(**values)


def _parse_provider(raw: Any) -> ProviderDecl:
    if not isinstance(raw, dict):
        raise _err("providers 的每一项都必须是对象")
    where = f"provider {raw.get('id')!r}"
    for key in ("id", "label", "protocol", "base_url"):
        if not str(raw.get(key, "")).strip():
            raise _err(f"{where} 缺少 {key}")
    auth_raw = raw.get("auth") or {}
    if not isinstance(auth_raw, dict):
        raise _err(f"{where} 的 auth 必须是对象")
    headers = raw.get("headers") or {}
    extra_body = raw.get("extra_body") or {}
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise _err(f"{where} 的 headers 必须是字符串到字符串的对象")
    if not isinstance(extra_body, dict):
        raise _err(f"{where} 的 extra_body 必须是对象")
    models_raw = raw.get("models") or []
    if not isinstance(models_raw, list):
        raise _err(f"{where} 的 models 必须是数组")
    models = []
    for item in models_raw:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise _err(f"{where} 有模型缺 id")
        for name in ("context_window", "max_output"):
            value = item.get(name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise _err(f"{where} 模型 {item['id']} 的 {name} 必须是正整数：{value!r}")
        models.append(
            ModelDecl(
                id=str(item["id"]),
                label=item.get("label"),
                context_window=item.get("context_window"),
                max_output=item.get("max_output"),
                capabilities=_parse_capabilities(item.get("capabilities"), f"{where} 模型 {item['id']}"),
            )
        )
    return ProviderDecl(
        id=str(raw["id"]),
        label=str(raw["label"]),
        protocol=str(raw["protocol"]),
        base_url=str(raw["base_url"]),
        auth=ModelAuth(
            type=str(auth_raw.get("type", AUTH_BEARER)),
            secret_ref=auth_raw.get("secret_ref"),
            header_name=auth_raw.get("header_name"),
        ),
        headers=dict(headers),
        extra_body=dict(extra_body),
        enabled=bool(raw.get("enabled", True)),
        models=tuple(models),
    )


def parse_byok(raw: Mapping[str, Any]) -> ByokConfig:
    """Validate structure and return the in-memory config; raises ConfigError."""
    providers_raw = raw.get("providers")
    bindings_raw = raw.get("bindings")
    if not isinstance(providers_raw, list) or not isinstance(bindings_raw, dict):
        raise _err("BYOK 配置必须有 providers（数组）与 bindings（对象）两块")
    providers: dict[str, ProviderDecl] = {}
    for item in providers_raw:
        provider = _parse_provider(item)
        if provider.id in providers:
            raise _err(f"provider id 重复：{provider.id}")
        providers[provider.id] = provider
    bindings: dict[str, str | None] = {}
    for slot in BINDING_SLOTS:
        value = bindings_raw.get(slot)
        if value is not None and not isinstance(value, str):
            raise _err(f"绑定 {slot} 必须是字符串或 null：{value!r}")
        bindings[slot] = value
    config = ByokConfig(providers=providers, bindings=bindings)
    validate_byok(config)
    return config


def load_byok(path: Path | None = None) -> ByokConfig | None:
    """Read the BYOK config; None = 没有配置（含 JSON 损坏，警告后回落 legacy）。"""
    target = path or config_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("BYOK 配置读不了（%s），回落旧配置：%s", target, exc)
        return None
    return parse_byok(raw)


# ---------------- secret 存取 ----------------


def read_secrets(path: Path | None = None) -> dict[str, str]:
    target = path or secrets_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("密钥文件读不了（%s）：%s", target, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("密钥文件不是对象（%s），当空处理", target)
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v}


def write_secrets(secrets: Mapping[str, str], path: Path | None = None) -> Path:
    return _write_json_atomic(path or secrets_path(), dict(secrets), owner_only=True)


def set_secret(ref: str, value: str) -> None:
    if not _ID_PATTERN.match(ref):
        raise _err(f"secret 引用只能用小写字母、数字与连字符：{ref!r}")
    secrets = read_secrets()
    secrets[ref] = value
    write_secrets(secrets)


def delete_secret(ref: str) -> None:
    secrets = read_secrets()
    if secrets.pop(ref, None) is not None:
        write_secrets(secrets)


# ---------------- 解析（角色 → 请求配置） ----------------


def _parallel_cap(env: Mapping[str, str] | None) -> int:
    """max_parallel_tool_calls 不是 per-provider 的，继续从 legacy 来源读。"""
    source = dict(os.environ if env is None else env)
    overlay = read_model_settings()
    for key, env_name in _SETTINGS_TO_ENV.items():
        value = overlay.get(key)
        if value:
            source[env_name] = value
    return _max_parallel_tool_calls(source)


def config_from_provider(provider: ProviderDecl, model_id: str, env=None) -> Config:
    """Resolve one provider+model into the runtime Config (secret plaintext included)."""
    secret = ""
    if provider.auth.type in (AUTH_BEARER, AUTH_HEADER):
        ref = provider.auth.secret_ref or provider.id
        secret = read_secrets().get(ref, "")
        if not secret:
            raise _err(
                f"缺少 {provider.label} 的密钥（secret_ref={ref!r}）："
                "在「设置 → 模型」里填入，或直接编辑 ~/.avid/secrets.json"
            )
    model = provider.model(model_id)
    capabilities = model.capabilities if model else ModelCapabilities()
    if capabilities.tool_calling is False:
        raise _err(
            f"模型 {provider.id}/{model_id} 声明不支持工具调用；agent 的 chat 槽位需要能调工具的模型"
        )
    extra_headers = dict(provider.headers)
    if provider.auth.type == AUTH_HEADER:
        extra_headers[provider.auth.header_name or ""] = secret
    window = model.context_window if model else None
    return Config(
        api_key=secret,
        base_url=provider.base_url,
        model=model_id,
        context_window=window if window is not None else window_for(model_id),
        provider=PROTOCOL_FAMILY[provider.protocol],
        max_parallel_tool_calls=_parallel_cap(env),
        extra_headers=extra_headers or None,
        extra_body=dict(provider.extra_body) or None,
        max_output=model.max_output if model else None,
    )


def _pick_provider(config: ByokConfig, provider_id: str) -> ProviderDecl:
    provider = config.providers.get(provider_id)
    if provider is None:
        raise _err(
            f"BYOK 配置里没有提供商 {provider_id!r}；可用：{'、'.join(sorted(config.providers))}"
        )
    if not provider.enabled:
        raise _err(f"提供商 {provider_id} 已停用，先在「设置 → 模型」里启用")
    return provider


def _resolve_ref(config: ByokConfig, ref: str, env=None) -> Config:
    match = _REF_PATTERN.match(ref)
    if match is None:
        raise _err(f"模型覆盖必须是 providerId/modelId 形式：{ref!r}")
    provider = _pick_provider(config, match.group(1))
    model_id = match.group(2)
    if provider.model(model_id) is None:
        raise _err(f"提供商 {provider.id} 下没有模型 {model_id!r}")
    return config_from_provider(provider, model_id, env)


def resolve_chat(model: str | None = None, env: Mapping[str, str] | None = None) -> Config:
    """唯一解析入口：本次覆盖 > chat 绑定 > legacy（load_config）。

    `model` 是「本次运行用哪个模型」：`providerId/modelId` ref 直接定位 BYOK；
    裸模型名先在 chat 绑定的提供商目录里找，找不到回落 legacy 的按名覆盖。
    `env` 只作用于 legacy 回落侧（BYOK 文件照常读取）。
    """
    override = (model or "").strip() or None
    byok = load_byok()
    if byok is not None:
        if override is not None and "/" in override:
            return _resolve_ref(byok, override, env)
        binding = byok.bindings.get(CHAT_SLOT)
        match = _REF_PATTERN.match(binding) if binding else None
        if match:
            provider = _pick_provider(byok, match.group(1))
            if override is None:
                return config_from_provider(provider, match.group(2), env)
            # 裸名覆盖：绑定提供商目录里有就按 BYOK 走，否则 legacy 的按名覆盖。
            if provider.model(override) is not None:
                return config_from_provider(provider, override, env)
    return load_config(env=env, model=override)


def byok_model_candidates() -> list[dict[str, str]]:
    """界面「按运行换模型」的 BYOK 候选：启用的提供商里 tool_calling≠false 的模型。"""
    byok = load_byok()
    if byok is None:
        return []
    candidates = []
    for provider in byok.providers.values():
        if not provider.enabled:
            continue
        for model in provider.models:
            if model.capabilities.tool_calling is False:
                continue
            candidates.append(
                {
                    "ref": f"{provider.id}/{model.id}",
                    "label": f"{provider.label} · {model.label or model.id}",
                }
            )
    return candidates


__all__ = [
    "AUTH_TYPES",
    "BINDING_SLOTS",
    "CHAT_SLOT",
    "ByokConfig",
    "ModelAuth",
    "ModelCapabilities",
    "ModelDecl",
    "PROTOCOLS",
    "ProviderDecl",
    "byok_model_candidates",
    "config_from_provider",
    "config_path",
    "delete_secret",
    "load_byok",
    "parse_byok",
    "read_secrets",
    "resolve_chat",
    "save_byok",
    "secrets_path",
    "set_secret",
    "validate_byok",
    "write_secrets",
]
