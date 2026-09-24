"""从环境变量读取并校验模型配置。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.openai.com/v1"

ENV_API_KEY = "AVID_API_KEY"
ENV_BASE_URL = "AVID_BASE_URL"
ENV_MODEL = "AVID_MODEL"
ENV_CONTEXT_WINDOW = "AVID_CONTEXT_WINDOW"
# 关掉"向 provider 问模型窗口"的探测（实现见 `ai/client.py::fetch_context_length`）。
# 取值 off / 0 / false / no 都算关；缺省 = 开。单测把它关掉，免得去打真实端点。
ENV_MODEL_INFO = "AVID_MODEL_INFO"
# 一步（模型一次回复）里最多同时跑几个工具调用。1 = 完全串行（改动前的行为）。
# 只有**并发安全**的工具会被并进同一段，写类/bash/任务类/子 agent 仍是串行屏障
# （分类见 `tools/safety.py`）。上限给死一个硬顶，防手误写个 1000 把磁盘打满。
ENV_MAX_PARALLEL_TOOL_CALLS = "AVID_MAX_PARALLEL_TOOL_CALLS"

REQUIRED = (ENV_API_KEY, ENV_MODEL)

#: 并发的默认值与硬上限。
DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10
MAX_PARALLEL_TOOL_CALLS_CEILING = 32

#: `ENV_MODEL_INFO` 的关闭取值（大小写无关）。
_MODEL_INFO_OFF = ("off", "0", "false", "no")


def model_info_enabled(source: Mapping[str, str] | None = None) -> bool:
    """要不要问 provider 的 `/models`。缺省开——它只在窗口查不到时才发一次请求。"""
    env = os.environ if source is None else source
    return env.get(ENV_MODEL_INFO, "").strip().lower() not in _MODEL_INFO_OFF

# 内置的模型窗口表（前缀匹配）。它只用来算「上下文占用率」这一个比值，所以
# **只列确定知道的值**，查不到一律回 None（界面显示 tokens 与「—」）。
#
# 为什么不做一个功能齐全的模型注册表：占用率的分母随模型版本变动（还受 beta 头、
# 服务端配置影响），维护一张"看起来全"的表会长期给出错误的分母——错的分母比没有
# 分母更糟。要精确值就设 AVID_CONTEXT_WINDOW（它优先于本表）。
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
    ("deepseek-chat", 65_536),
    ("deepseek-reasoner", 65_536),
    ("gemini-1.5", 1_048_576),
    ("gemini-2.0", 1_048_576),
    ("gemini-2.5", 1_048_576),
)


def window_for(model: str) -> int | None:
    """按模型名查内置窗口表。最长前缀优先，避免 ``gpt-4`` 这类短前缀抢占。"""
    name = model.strip().lower()
    matched: int | None = None
    best = -1
    for prefix, window in MODEL_CONTEXT_WINDOWS:
        if name.startswith(prefix) and len(prefix) > best:
            best = len(prefix)
            matched = window
    return matched


class ConfigError(Exception):
    """配置缺失或非法。错误信息自带修复方法。"""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    # 模型上下文窗口（tokens）。None = 不认识这个模型，占用率因此不可计算——
    # 调用方显示 tokens 数与「—」，不做任何换算猜测。
    context_window: int | None = None
    # 一步内并行工具调用的上限。1 = 完全串行。只会影响**并发安全**的工具；
    # 写类调用是屏障，永远单独跑（`tools/safety.py`）。
    max_parallel_tool_calls: int = DEFAULT_MAX_PARALLEL_TOOL_CALLS

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    source = os.environ if env is None else env

    missing = [name for name in REQUIRED if not source.get(name, "").strip()]
    if missing:
        raise ConfigError(
            "缺少必需的环境变量：" + "、".join(missing) + "\n"
            "设置方法：cp .env.example .env 填入真实值，然后运行\n"
            '  uv run --env-file .env avid "你好"'
        )

    model = source[ENV_MODEL].strip()
    return Config(
        api_key=source[ENV_API_KEY].strip(),
        base_url=source.get(ENV_BASE_URL, "").strip() or DEFAULT_BASE_URL,
        model=model,
        context_window=_context_window(source, model),
        max_parallel_tool_calls=_max_parallel_tool_calls(source),
    )


def _max_parallel_tool_calls(source: Mapping[str, str]) -> int:
    """一步内的并发上限。缺省 10。

    与 `_context_window` 同一个失败模型：写了非法值就报错，不静默回落——用户设了 4
    却按 10 跑，比报错更难查。1 是合法值（完全串行），大于硬上限同样报错而不是夹取，
    否则"我设了 1000"与"实际跑 32"之间的差会一直藏着。
    """
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
    """显式配置优先于内置表。

    显式值非法时**报错而不是回落**：``AVID_CONTEXT_WINDOW=128k`` 这种写法要是被
    静默忽略，用户会以为自己在看一个真实的分母，实际看的是内置表算出来的另一个数。
    """
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
