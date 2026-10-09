"""Model protocol layer: dispatch to a provider module plus the protocol registry.

Adding a protocol is one implementation module and one entry in PROVIDERS.
"""

from __future__ import annotations

from . import anthropic, openai_compat, responses

PROVIDERS: dict[str, object] = {
    "openai": openai_compat,
    "anthropic": anthropic,
    "responses": responses,
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

