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
# 一次运行最多允许几轮「模型 → 工具」。**缺省无上限**：循环的正常终止条件是模型不再
# 请求工具，而不是耗尽一个预算数字。旧值写死 8，实测把「读 9 个文件」这种普通任务
# 判成失败。需要成本闸门时显式设一个正整数。
ENV_MAX_ROUNDS = "AVID_MAX_ROUNDS"

REQUIRED = (ENV_API_KEY, ENV_MODEL)

#: `ENV_MODEL_INFO` 的关闭取值（大小写无关）。
_MODEL_INFO_OFF = ("off", "0", "false", "no")

#: `ENV_MAX_ROUNDS` 表示「无上限」的取值（大小写无关）；未设同样是无上限。
_MAX_ROUNDS_UNLIMITED = ("", "0", "off", "false", "no", "none", "unlimited")


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
    # 一次运行的轮数上限（一轮 = 一次模型调用 + 它请求的那批工具）。None = 无上限，
    # 也是缺省：循环靠"模型不再请求工具"收敛，不靠预算数字。设置见 `ENV_MAX_ROUNDS`。
    max_rounds: int | None = None

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
        max_rounds=_max_rounds(source),
    )


def _max_rounds(source: Mapping[str, str]) -> int | None:
    """轮数上限：未设或明确的"关"取值一律 = 无上限。

    与 `_context_window` 采取同样的失败模型：写了非法值就报错，不静默回落成无上限。
    ``AVID_MAX_ROUNDS=无`` 被忽略掉的话，用户会以为自己设了一道闸门，实际没有。
    """
    raw = source.get(ENV_MAX_ROUNDS, "").strip()
    if raw.lower() in _MAX_ROUNDS_UNLIMITED:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{ENV_MAX_ROUNDS} 必须是正整数（轮数），或 0/off 表示无上限：{raw!r}\n"
            "例如 AVID_MAX_ROUNDS=40（缺省不设 = 无上限）"
        ) from exc
    if value <= 0:
        raise ConfigError(
            f"{ENV_MAX_ROUNDS} 必须大于 0：{value}（要无上限请设 0 或删掉这一项）"
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
