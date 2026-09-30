"""Builds the one function-calling envelope every tool schema is wrapped in.

``strict`` stays off because not every compatible endpoint supports it.

"""


from __future__ import annotations

from typing import Any

Property = dict[str, Any]


def tool(
    name: str,
    description: str,
    properties: dict[str, Property],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Wraps one function tool, whose arguments are always a single flat object."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }
