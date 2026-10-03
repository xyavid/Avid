"""Provider registry mapping a protocol name to its implementation module."""

from __future__ import annotations

from ..protocol import LLMError
from . import anthropic, gemini, openai_compat

# Single registry: adding a protocol is one module plus one entry here.
PROVIDERS: dict[str, object] = {
    "openai": openai_compat,
    "anthropic": anthropic,
    "gemini": gemini,
}


def impl(name: str):
    """Return the provider module for name, raising LLMError for an unknown one."""
    module = PROVIDERS.get(name)
    if module is None:
        raise LLMError(
            f"未知 provider {name!r}；可用：{'、'.join(sorted(PROVIDERS))}"
        )
    return module


__all__ = ["PROVIDERS", "anthropic", "gemini", "impl", "openai_compat"]
