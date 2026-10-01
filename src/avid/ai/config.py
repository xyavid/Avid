"""Read and validate model configuration from environment variables.

界面写入的覆盖层（`~/.avid/model.toml`，由「设置 → 模型」表单维护）**优先于**
环境变量：界面是最近的显式动作，.env 是初始来源。两者都没有时给出可执行的修复文案。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("avid.ai.config")

DEFAULT_BASE_URL = "https://api.openai.com/v1"

ENV_API_KEY = "AVID_API_KEY"
ENV_BASE_URL = "AVID_BASE_URL"
ENV_MODEL = "AVID_MODEL"
ENV_CONTEXT_WINDOW = "AVID_CONTEXT_WINDOW"
# Protocol-family override; when unset the family is detected from base_url.
ENV_PROVIDER = "AVID_PROVIDER"
# Set to off/0/false/no to disable the provider model-window probe; tests keep it off.
ENV_MODEL_INFO = "AVID_MODEL_INFO"
# Per-step cap on parallel tool calls; 1 means fully serial and only concurrency-safe tools overlap.
ENV_MAX_PARALLEL_TOOL_CALLS = "AVID_MAX_PARALLEL_TOOL_CALLS"

REQUIRED = (ENV_API_KEY, ENV_MODEL)

#: Default and hard ceiling for the number of tool calls run in parallel within one step.
DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10
MAX_PARALLEL_TOOL_CALLS_CEILING = 32

#: Values of ENV_MODEL_INFO that disable the probe (case-insensitive).
_MODEL_INFO_OFF = ("off", "0", "false", "no")

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GEMINI = "gemini"
#: Allowed values; the registry in providers/ holds the matching protocol implementations.
PROVIDERS = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_GEMINI)

#: Default endpoint per protocol family when AVID_BASE_URL is unset.
DEFAULT_BASE_URLS: dict[str, str] = {
    PROVIDER_OPENAI: DEFAULT_BASE_URL,
    PROVIDER_ANTHROPIC: "https://api.anthropic.com",
    PROVIDER_GEMINI: "https://generativelanguage.googleapis.com/v1beta",
}

#: Location of the UI overlay file; an env override exists so tests stay off the real home.
ENV_SETTINGS_PATH = "AVID_MODEL_CONFIG"
DEFAULT_SETTINGS_PATH = Path.home() / ".avid" / "model.toml"
#: The file uses the UI's short field names; the merge maps them onto the env names.
_SETTINGS_TO_ENV = {
    "api_key": ENV_API_KEY,
    "base_url": ENV_BASE_URL,
    "model": ENV_MODEL,
    "provider": ENV_PROVIDER,
    "context_window": ENV_CONTEXT_WINDOW,
    "max_parallel_tool_calls": ENV_MAX_PARALLEL_TOOL_CALLS,
}


def settings_path() -> Path:
    """Resolve the overlay file location: AVID_MODEL_CONFIG override or ~/.avid/model.toml."""
    raw = os.environ.get(ENV_SETTINGS_PATH, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_SETTINGS_PATH


def read_model_settings(path: Path | None = None) -> dict[str, str]:
    """Read the UI overlay as short-named fields; a missing or corrupt file degrades to {}."""
    target = path or settings_path()
    if not target.is_file():
        return {}
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("界面模型配置读不了（%s），回落环境变量：%s", target, exc)
        return {}
    values: dict[str, str] = {}
    for key in _SETTINGS_TO_ENV:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            values[key] = value.strip()
        elif isinstance(value, int) and not isinstance(value, bool):
            values[key] = str(value)
    return values


def write_model_settings(values: Mapping[str, str], path: Path | None = None) -> Path:
    """Write the overlay atomically with owner-only permissions (it may hold an API key)."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in values.items() if value.strip())
    handle, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".model-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return target


def clear_model_settings(path: Path | None = None) -> bool:
    """Drop the overlay so the environment variables take over again."""
    target = path or settings_path()
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def detect_provider(base_url: str) -> str:
    """Infer the protocol family from the endpoint host, defaulting to OpenAI-compatible."""
    host = base_url.lower()
    if "anthropic" in host:
        return PROVIDER_ANTHROPIC
    if "generativelanguage" in host:
        return PROVIDER_GEMINI
    return PROVIDER_OPENAI


def model_info_enabled(source: Mapping[str, str] | None = None) -> bool:
    """Report whether the provider model-window probe is enabled."""
    env = os.environ if source is None else source
    return env.get(ENV_MODEL_INFO, "").strip().lower() not in _MODEL_INFO_OFF

# Built-in context windows by model-name prefix, listing only values that are certainly known.
MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-4.1", 1_047_576),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("o4-mini", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-opus-4", 200_000),
    ("claude-3", 200_000),
    # Family fallbacks; the longest matching prefix wins, so the specific entries above still apply.
    ("claude-", 200_000),
    ("deepseek-chat", 65_536),
    ("deepseek-reasoner", 65_536),
    ("gemini-1.5", 1_048_576),
    ("gemini-2.0", 1_048_576),
    ("gemini-2.5", 1_048_576),
    ("gemini-", 1_048_576),
)


def window_for(model: str) -> int | None:
    """Look up the built-in window table by longest matching model-name prefix."""
    name = model.strip().lower()
    matched: int | None = None
    best = -1
    for prefix, window in MODEL_CONTEXT_WINDOWS:
        if name.startswith(prefix) and len(prefix) > best:
            best = len(prefix)
            matched = window
    return matched


class ConfigError(Exception):
    """Configuration is missing or invalid; the message states how to repair it."""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    # Context window in tokens; None means unknown, so the usage ratio is not computed.
    context_window: int | None = None
    # Protocol family (openai / anthropic / gemini); None means it is detected from base_url.
    provider: str | None = None
    # Per-step cap on parallel tool calls; 1 is fully serial and unsafe tools never overlap.
    max_parallel_tool_calls: int = DEFAULT_MAX_PARALLEL_TOOL_CALLS

    @property
    def resolved_provider(self) -> str:
        """Protocol family in effect: the explicit override or the one detected from base_url."""
        name = self.provider or detect_provider(self.base_url)
        if name not in PROVIDERS:
            raise ConfigError(
                f"未知 provider {name!r}；可用：{'、'.join(PROVIDERS)}"
            )
        return name

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def load_config(env: Mapping[str, str] | None = None, *, overlay: Mapping[str, str] | None = None) -> Config:
    """Build a Config from env plus the UI overlay (overlay wins); overlay={} skips the file."""
    source = os.environ if env is None else env
    raw_overlay = read_model_settings() if overlay is None else dict(overlay)
    file_values = {
        _SETTINGS_TO_ENV[key]: value for key, value in raw_overlay.items() if key in _SETTINGS_TO_ENV
    }
    merged: dict[str, str] = {**source, **file_values}

    missing = [name for name in REQUIRED if not merged.get(name, "").strip()]
    if missing:
        raise ConfigError(
            "缺少模型配置：" + "、".join(missing) + "\n"
            "设置方法：在界面「设置 → 模型」里填写，或 cp .env.example .env 填入真实值后运行\n"
            '  uv run --env-file .env avid "你好"'
        )

    model = merged[ENV_MODEL].strip()
    # An explicit provider picks its default endpoint; detection follows base_url implicitly.
    raw_base = merged.get(ENV_BASE_URL, "").strip()
    raw_provider = merged.get(ENV_PROVIDER, "").strip()
    if raw_provider and raw_provider not in PROVIDERS:
        raise ConfigError(
            f"{ENV_PROVIDER} 必须是 {'、'.join(PROVIDERS)} 之一：{raw_provider!r}"
        )
    provider = raw_provider or None
    base_url = raw_base or DEFAULT_BASE_URLS[provider or PROVIDER_OPENAI]
    return Config(
        api_key=merged[ENV_API_KEY].strip(),
        base_url=base_url,
        model=model,
        context_window=_context_window(merged, model),
        provider=provider,
        max_parallel_tool_calls=_max_parallel_tool_calls(merged),
    )


def _max_parallel_tool_calls(source: Mapping[str, str]) -> int:
    """Resolve the per-step parallel cap; illegal values raise rather than falling back."""
    raw = source.get(ENV_MAX_PARALLEL_TOOL_CALLS, "").strip()
    if not raw:
        return DEFAULT_MAX_PARALLEL_TOOL_CALLS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{ENV_MAX_PARALLEL_TOOL_CALLS} 必须是正整数：{raw!r}\n"
            f"例如 {ENV_MAX_PARALLEL_TOOL_CALLS}=4；1 = 完全串行"
        ) from exc
    if value < 1:
        raise ConfigError(
            f"{ENV_MAX_PARALLEL_TOOL_CALLS} 必须 ≥ 1：{value}（1 = 完全不并发）"
        )
    if value > MAX_PARALLEL_TOOL_CALLS_CEILING:
        raise ConfigError(
            f"{ENV_MAX_PARALLEL_TOOL_CALLS} 不能超过硬上限 "
            f"{MAX_PARALLEL_TOOL_CALLS_CEILING}：{value}"
        )
    return value


def _context_window(source: Mapping[str, str], model: str) -> int | None:
    """Resolve the context window; an explicit value wins over the table and must be valid."""
    raw = source.get(ENV_CONTEXT_WINDOW, "").strip()
    if not raw:
        return window_for(model)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{ENV_CONTEXT_WINDOW} 必须是正整数（tokens）：{raw!r}\n"
            "例如 AVID_CONTEXT_WINDOW=200000"
        ) from exc
    if value <= 0:
        raise ConfigError(f"{ENV_CONTEXT_WINDOW} 必须大于 0：{value}")
    return value
