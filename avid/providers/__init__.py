"""Model protocol layer: config, provider dispatch, and the paired tool-call message invariant.

Provider 注册表也在本模块：新增一个协议 = 一个实现模块 + PROVIDERS 里一条表项。
"""

from __future__ import annotations

from . import anthropic, gemini, openai_compat

# Single registry: adding a protocol is one module plus one entry here.
PROVIDERS: dict[str, object] = {
    "openai": openai_compat,
    "anthropic": anthropic,
    "gemini": gemini,
}


def impl(name: str):
    """Return the provider module for name, raising LLMError for an unknown one."""
    from .protocol import LLMError

    module = PROVIDERS.get(name)
    if module is None:
        raise LLMError(
            f"未知 provider {name!r}；可用：{'、'.join(sorted(PROVIDERS))}"
        )
    return module

