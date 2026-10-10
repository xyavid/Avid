"""Reasoning (CoT) records on assistant messages, and the single place deciding if they go out.

A record is written by the protocol module that produced the turn, so it belongs to that route
(providerId/modelId + protocol family); it is read back only by the same route — keep what the same
model needs, drop what another model cannot own, and never downgrade thinking text into the prompt.
Replay happens only where the protocol requires continuity (an assistant message answered by a tool
result in the same turn); everything else stays in the log, unsent and untruncated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import Config

#: Message key holding the record; the kernel, the UI and the index treat it as opaque.
RECORD_KEY = "reasoning"


@dataclass(frozen=True)
class Route:
    """Who produced a reasoning record: provider id, model id and protocol family."""

    provider_id: str
    model: str
    protocol: str

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model}"


def route_of(config: Config) -> Route:
    return Route(
        provider_id=config.provider_id or "",
        model=config.model,
        protocol=config.resolved_provider,
    )


def attach(message: dict[str, Any], blocks: Sequence[Mapping[str, Any]], *, route: Route) -> None:
    """Record this turn's reasoning on its message; no blocks means no record."""
    kept = [dict(block) for block in blocks if block]
    if not kept:
        return
    message[RECORD_KEY] = {"route": route.key, "protocol": route.protocol, "blocks": kept}


def record_of(message: Mapping[str, Any]) -> dict[str, Any] | None:
    record = message.get(RECORD_KEY)
    return record if isinstance(record, dict) else None


def text_of(blocks: Sequence[Mapping[str, Any]]) -> str:
    """Readable text of a record, for the families whose wire field is one plain string."""
    return "".join(str(block.get("text") or "") for block in blocks)


def plan_replay(
    messages: Sequence[Mapping[str, Any]], config: Config
) -> dict[int, list[dict[str, Any]]]:
    """Index → blocks to send with this request.

    Two conditions, and both must hold: the record's route is this request's route, and the message
    is answered by a tool result in the same turn (think → tool_call → tool_result). Anything else
    is deliberately left behind — a previous turn's CoT must not be re-fed to a new question, and a
    record from another model must not ride along with this one.
    """
    route = route_of(config)
    plan: dict[int, list[dict[str, Any]]] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        record = record_of(message)
        if record is None:
            continue
        if record.get("route") != route.key or record.get("protocol") != route.protocol:
            continue
        following = messages[index + 1] if index + 1 < len(messages) else None
        if not (isinstance(following, Mapping) and following.get("role") == "tool"):
            continue
        blocks = record.get("blocks")
        if isinstance(blocks, list):
            kept = [dict(block) for block in blocks if isinstance(block, Mapping)]
            if kept:
                plan[index] = kept
    return plan
