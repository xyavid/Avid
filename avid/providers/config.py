"""Runtime model vocabulary: the `Config` carrier, context-window table and knobs.

模型**连接**（端点 / 协议 / 密钥 / 模型名）的唯一来源是 BYOK 配置（`ai/byok.py`，
`~/.avid/models.json` + `~/.avid/secrets.json`）；本模块不再承载任何模型身份配置，
只保留模型无关的运行期事实：`Config` 载体、上下文窗口内置表与两个旁路开关
（窗口探测 `AVID_MODEL_INFO`、一步并行工具上限 `AVID_MAX_PARALLEL_TOOL_CALLS`）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Set to off/0/false/no to disable the provider model-window probe; tests keep it off.
ENV_MODEL_INFO = "AVID_MODEL_INFO"
# Per-step cap on parallel tool calls; 1 means fully serial and only concurrency-safe tools overlap.
ENV_MAX_PARALLEL_TOOL_CALLS = "AVID_MAX_PARALLEL_TOOL_CALLS"

#: Default and hard ceiling for the number of tool calls run in parallel within one step.
DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10
MAX_PARALLEL_TOOL_CALLS_CEILING = 32

#: Values of ENV_MODEL_INFO that disable the probe (case-insensitive).
_MODEL_INFO_OFF = ("off", "0", "false", "no")

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_RESPONSES = "responses"
#: Allowed values; the registry in providers/ holds the matching protocol implementations.
PROVIDERS = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_RESPONSES)

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
    # Protocol family (openai / anthropic / responses); the BYOK resolver fills it from the protocol.
    provider: str | None = None
    # Per-step cap on parallel tool calls; 1 is fully serial and unsafe tools never overlap.
    max_parallel_tool_calls: int = DEFAULT_MAX_PARALLEL_TOOL_CALLS
    # BYOK passthrough (ai/byok.py): extra request headers (custom auth), fixed body fields
    # and the per-model output cap; None = untouched behavior.
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    max_output: int | None = None
    # 本次运行选的推理强度（来自运行请求，取值必须在模型声明的档位列表里）；
    # None = 这次不带这个参数。
    reasoning_effort: str | None = None

    @property
    def resolved_provider(self) -> str:
        """Protocol family in effect; BYOK resolution always sets it explicitly."""
        name = self.provider or PROVIDER_OPENAI
        if name not in PROVIDERS:
            raise ConfigError(f"未知 provider {name!r}；可用：{'、'.join(PROVIDERS)}")
        return name

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def model_info_enabled(source: Mapping[str, str] | None = None) -> bool:
    """Report whether the provider model-window probe is enabled."""
    env = os.environ if source is None else source
    return env.get(ENV_MODEL_INFO, "").strip().lower() not in _MODEL_INFO_OFF


def max_parallel_tool_calls(source: Mapping[str, str] | None = None) -> int:
    """Resolve the per-step parallel cap; illegal values raise rather than falling back."""
    env = os.environ if source is None else source
    raw = env.get(ENV_MAX_PARALLEL_TOOL_CALLS, "").strip()
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
