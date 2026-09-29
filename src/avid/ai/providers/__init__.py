"""provider 注册与分发：名字 → 协议模块。

每家协议模块暴露同签名的 ``chat`` / ``stream``；门面（``ai/client.py``）按
``config.resolved_provider`` 从这里取实现。加一家协议 = 加一个模块 + 一行注册，
门面与循环不动。
"""

from __future__ import annotations

from ..protocol import LLMError
from . import anthropic, gemini, openai_compat

PROVIDERS: dict[str, object] = {
    "openai": openai_compat,
    "anthropic": anthropic,
    "gemini": gemini,
}


def impl(name: str):
    """取一个 provider 模块。名字不认识是配置错误，跑起来前就该发现。"""
    module = PROVIDERS.get(name)
    if module is None:
        raise LLMError(
            f"未知 provider {name!r}；可用：{'、'.join(sorted(PROVIDERS))}"
        )
    return module


__all__ = ["PROVIDERS", "anthropic", "gemini", "impl", "openai_compat"]
