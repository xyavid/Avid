"""BYOK connectivity check that answers "will this work" at configuration time.

Two probes: a minimal chat call (`max_tokens=1`) verifying key, endpoint and network, and a tool
smoke test whose required-argument `get_time` definition forces a real tool_calls reply; error
classification recognizes only known LLMError signals (all adapters write `HTTP <status> — body`),
echoes unknown messages verbatim and reports a ConfigError instead of raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import byok
from .byok import ProviderDecl
from .client import chat_completion
from .config import ConfigError
from .protocol import LLMError

#: Smoke tool definition: the required argument forces a real tool_call instead of a text answer.
SMOKE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "查询当前时间。",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "时区名，例如 local"},
            },
            "required": ["timezone"],
        },
    },
}

_HTTP_PATTERN = re.compile(r"HTTP (\d{3})")


@dataclass(frozen=True)
class VerifyStep:
    step: str  # "chat" | "tool"
    ok: bool
    detail: str


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    steps: tuple[VerifyStep, ...]


def classify_error(message: str) -> str:
    """One known signal per failure family; unknown messages pass through verbatim."""
    match = _HTTP_PATTERN.search(message)
    status = int(match.group(1)) if match else None
    if status in (401, 403):
        return "密钥无效、过期或没有权限"
    if status == 404:
        return "端点路径不存在：多半是 base_url 少了 /v1（OpenAI 兼容端点要以 /v1 结尾）"
    if status == 429:
        return "触发限流或额度用尽：换个 key 或稍后再试"
    lowered = message.lower()
    if (
        "timed out" in lowered
        or "timeout" in lowered
        or "connect" in lowered
        or "network" in lowered
    ):
        return "连不上端点：检查地址、代理与网络"
    return message


def _fail_detail(message: str) -> str:
    reason = classify_error(message)
    excerpt = message[:200]
    return reason if reason == message else f"{reason}（{excerpt}）"


def verify_provider(
    provider: ProviderDecl, model_id: str, *, secret: str | None = None
) -> VerifyReport:
    """Run both probes against a real endpoint and report expected failures instead of raising;
    ``secret`` overrides the secret store so the settings panel can test before saving, with no
    file-write side effect.
    """
    try:
        config = byok.config_from_provider(provider, model_id, secret=secret)
    except ConfigError as exc:
        return VerifyReport(False, (VerifyStep("chat", False, str(exc)),))
    steps: list[VerifyStep] = []

    try:
        chat_completion(
            config, [{"role": "user", "content": "请回复 ok"}], max_tokens=1, tools=None
        )
        steps.append(VerifyStep("chat", True, "密钥（如已配置）、端点与网络可用"))
    except LLMError as exc:
        steps.append(VerifyStep("chat", False, _fail_detail(str(exc))))
        return VerifyReport(False, tuple(steps))

    try:
        turn = chat_completion(
            config,
            [
                {
                    "role": "user",
                    "content": "现在几点？必须调用 get_time 工具查询，timezone 填 local。",
                }
            ],
            tools=[dict(SMOKE_TOOL)],
            max_tokens=256,
        )
    except LLMError as exc:
        steps.append(VerifyStep("tool", False, _fail_detail(str(exc))))
        return VerifyReport(False, tuple(steps))

    if turn.tool_calls:
        steps.append(VerifyStep("tool", True, "模型正确返回了工具调用"))
    else:
        steps.append(
            VerifyStep(
                "tool",
                False,
                "模型没有返回 tool_calls：它可能不支持工具调用，只能聊天，接进 agent 无法干活",
            )
        )
    return VerifyReport(all(step.ok for step in steps), tuple(steps))


__all__ = ["SMOKE_TOOL", "VerifyReport", "VerifyStep", "classify_error", "verify_provider"]
