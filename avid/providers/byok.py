"""BYOK model config, the only source of model connections: `~/.avid/models.json` holds the
providers + bindings pair and stores secret references only, while `~/.avid/secrets.json` maps
secret_ref to the plaintext key and is written 0600 via a temp file and os.replace.

A JSON parse failure degrades to "no config" with a warning, while a structurally invalid file
raises ConfigError instead of silently switching endpoints.
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

from .config import Config, ConfigError, max_parallel_tool_calls, window_for

logger = logging.getLogger("avid.providers.byok")

ENV_BYOK_CONFIG = "AVID_BYOK_CONFIG"
ENV_BYOK_SECRETS = "AVID_BYOK_SECRETS"

PROTOCOL_OPENAI_COMPATIBLE = "openai-compatible"
PROTOCOL_OLLAMA = "ollama"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_RESPONSES = "responses"
#: Protocols mapped to registry families; ollama uses its OpenAI-compatible /v1 endpoint.
PROTOCOLS = (
    PROTOCOL_OPENAI_COMPATIBLE,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_RESPONSES,
    PROTOCOL_OLLAMA,
)
PROTOCOL_FAMILY = {
    PROTOCOL_OPENAI_COMPATIBLE: "openai",
    PROTOCOL_OLLAMA: "openai",
    PROTOCOL_ANTHROPIC: "anthropic",
    PROTOCOL_RESPONSES: "responses",
}
DEFAULT_PROTOCOL_BASE_URLS = {
    PROTOCOL_OPENAI_COMPATIBLE: "https://api.openai.com/v1",
    PROTOCOL_OLLAMA: "http://localhost:11434/v1",
    PROTOCOL_ANTHROPIC: "https://api.anthropic.com",
    PROTOCOL_RESPONSES: "https://api.openai.com/v1",
}

#: The only role slot; a binding is "providerId/modelId" and null means unbound.
CHAT_SLOT = "chat"
BINDING_SLOTS = (CHAT_SLOT,)

_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
_REF_PATTERN = re.compile(r"^([a-z0-9-]+)/(.+)$")

CAPABILITY_FIELDS = ("tool_calling", "vision", "json_mode", "streaming", "reasoning")

#: Effort levels come from the model's declared list and go out verbatim; the kernel keeps no enum.
MAX_EFFORT_CHARS = 32


def config_path() -> Path:
    raw = os.environ.get(ENV_BYOK_CONFIG, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".avid" / "models.json"


def secrets_path() -> Path:
    raw = os.environ.get(ENV_BYOK_SECRETS, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".avid" / "secrets.json"


# ---------------- Model declarations (in memory) ----------------


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
    #: Effort levels this model accepts, offered in the UI; empty means the parameter is not sent.
    reasoning_efforts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderDecl:
    id: str
    label: str
    protocol: str
    base_url: str
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


# ---------------- Validation ----------------


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
    if provider.models and len({m.id for m in provider.models}) != len(provider.models):
        raise _err(f"provider {provider.id} 有重复的模型 id")
    for model in provider.models:
        if not model.id.strip():
            raise _err(f"provider {provider.id} 有模型缺 id")
        for name in ("context_window", "max_output"):
            value = getattr(model, name)
            if value is not None and value < 1:
                raise _err(f"provider {provider.id} 模型 {model.id} 的 {name} 必须 ≥ 1：{value}")
        if len(set(model.reasoning_efforts)) != len(model.reasoning_efforts):
            raise _err(f"provider {provider.id} 模型 {model.id} 的 reasoning_efforts 有重复档位")
        for level in model.reasoning_efforts:
            if not level.strip() or len(level) > MAX_EFFORT_CHARS:
                raise _err(
                    f"provider {provider.id} 模型 {model.id} 的 reasoning_efforts 里每一档"
                    f"必须是 1–{MAX_EFFORT_CHARS} 字符的非空字符串：{level!r}"
                )


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
        bound = config.providers.get(pid)
        if bound is None:
            raise _err(f"绑定 {slot} 指向不存在的提供商：{pid}")
        if not bound.enabled:
            raise _err(f"绑定 {slot} 指向已停用的提供商：{pid}")
        model = bound.model(mid)
        if model is None:
            raise _err(f"绑定 {slot} 指向 {pid} 下不存在的模型：{binding}")
        if model.capabilities.tool_calling is False:
            raise _err(
                f"绑定 {slot} 的模型 {binding} 声明不支持工具调用；"
                "chat 槽位需要能调工具的模型，否则「能聊天、一干活就废」"
            )


# ---------------- File I/O ----------------


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
        "headers": provider.headers,
        "extra_body": provider.extra_body,
        "enabled": provider.enabled,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "context_window": m.context_window,
                "max_output": m.max_output,
                "reasoning_efforts": list(m.reasoning_efforts),
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
    # A legacy auth block is silently ignored: auth is implicit now and old files must keep loading.
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
        raw_efforts = item.get("reasoning_efforts", item.get("reasoning_effort"))
        if raw_efforts is None:
            efforts: tuple[str, ...] = ()
        elif isinstance(raw_efforts, str):
            # A single string in a hand-edited file is accepted as a one-element list.
            efforts = (raw_efforts,)
        elif isinstance(raw_efforts, list) and all(isinstance(x, str) for x in raw_efforts):
            efforts = tuple(raw_efforts)
        else:
            raise _err(
                f"{where} 模型 {item['id']} 的 reasoning_efforts 必须是字符串数组：{raw_efforts!r}"
            )
        models.append(
            ModelDecl(
                id=str(item["id"]),
                label=item.get("label"),
                context_window=item.get("context_window"),
                max_output=item.get("max_output"),
                capabilities=_parse_capabilities(
                    item.get("capabilities"), f"{where} 模型 {item['id']}"
                ),
                reasoning_efforts=efforts,
            )
        )
    return ProviderDecl(
        id=str(raw["id"]),
        label=str(raw["label"]),
        protocol=str(raw["protocol"]),
        base_url=str(raw["base_url"]),
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
    """Read the BYOK config; None means no usable one (a parse failure warns and degrades)."""
    target = path or config_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("BYOK 配置读不了（%s），回落旧配置：%s", target, exc)
        return None
    return parse_byok(raw)


# ---------------- Secret storage ----------------


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


# ---------------- Resolution (role -> request config) ----------------


def _parallel_cap(env: Mapping[str, str] | None) -> int:
    """The parallel cap is a run-time switch, not per-provider, so it is read from the env."""
    return max_parallel_tool_calls(env)


def config_from_provider(
    provider: ProviderDecl,
    model_id: str,
    env=None,
    *,
    secret: str | None = None,
    effort: str | None = None,
) -> Config:
    """Resolve one provider+model into the runtime Config (secret plaintext included); ``secret``
    overrides the secret store so an unsaved key can be probed without file writes, and auth is
    implicit — a key stored under the provider id is sent with the protocol's standard header,
    while a missing one means no auth header, which local endpoints rely on.
    """
    if secret is None:
        secret = read_secrets().get(provider.id, "")
    model = provider.model(model_id)
    capabilities = model.capabilities if model else ModelCapabilities()
    if effort is not None:
        declared = model.reasoning_efforts if model else ()
        if effort not in declared:
            available = "、".join(declared) if declared else "（该模型没有声明任何档位）"
            raise _err(
                f"模型 {provider.id}/{model_id} 没有声明推理强度 {effort!r}；可用：{available}"
            )
    if capabilities.tool_calling is False:
        raise _err(
            f"模型 {provider.id}/{model_id} 声明不支持工具调用；agent 的 chat 槽位需要能调工具的模型"
        )
    extra_headers = dict(provider.headers)
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
        reasoning_effort=effort,
        vision=capabilities.vision,
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


def _resolve_ref(config: ByokConfig, ref: str, env=None, *, effort: str | None = None) -> Config:
    match = _REF_PATTERN.match(ref)
    if match is None:
        raise _err(f"模型覆盖必须是 providerId/modelId 形式：{ref!r}")
    provider = _pick_provider(config, match.group(1))
    model_id = match.group(2)
    if provider.model(model_id) is None:
        raise _err(f"提供商 {provider.id} 下没有模型 {model_id!r}")
    return config_from_provider(provider, model_id, env, effort=effort)

_NO_CONFIG_MESSAGE = (
    "还没有模型配置：在界面「设置 → 模型」里添加提供商并绑定 chat 槽位，\n"
    "或手编 ~/.avid/models.json（providers + bindings，结构见 ai/byok.py 模块注释），\n"
    "密钥放 ~/.avid/secrets.json"
)


def resolve_chat(
    model: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    effort: str | None = None,
) -> Config:
    """The single resolution entry point: this run's override wins over the chat binding, and no
    usable binding raises ConfigError; ``model`` is a providerId/modelId ref or a bare model name
    looked up in the bound provider only, while ``env`` only supplies the parallel-cap source.
    """
    override = (model or "").strip() or None
    chosen = (effort or "").strip() or None
    byok = load_byok()
    if byok is None:
        raise _err(_NO_CONFIG_MESSAGE)
    if override is not None and "/" in override:
        return _resolve_ref(byok, override, env, effort=chosen)
    binding = byok.bindings.get(CHAT_SLOT)
    if not binding:
        raise _err(
            "chat 槽位还没有绑定模型：在「设置 → 模型」的 chat 下拉里选一个，"
            "或把 bindings.chat 设为 providerId/modelId"
        )
    match = _REF_PATTERN.match(binding)
    if match is None:  # Unreachable through parse_byok; guards a hand-edited config path.
        raise _err(f"绑定 chat 的值必须是 providerId/modelId 形式：{binding!r}")
    provider = _pick_provider(byok, match.group(1))
    model_id = match.group(2)
    if override is not None:
        if provider.model(override) is None:
            raise _err(
                f"模型 {override!r} 不在绑定的提供商 {provider.id} 里："
                "先在「设置 → 模型」里给它加上，或用 providerId/modelId 形式指定其他提供商的模型"
            )
        model_id = override
    return config_from_provider(provider, model_id, env, effort=chosen)


def byok_model_candidates() -> list[dict[str, Any]]:
    """BYOK candidates for per-run model switching: enabled providers' models with tool_calling not
    false, each carrying its declared reasoning levels.
    """
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
                    "reasoning_efforts": list(model.reasoning_efforts),
                }
            )
    return candidates


__all__ = [
    "BINDING_SLOTS",
    "CHAT_SLOT",
    "ByokConfig",
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
