"""Turns one tool call into one verdict, in this fixed order: credential reads are hard-denied
(secrets in context cannot be recalled); catastrophic commands are asked once and denied when no ask
channel exists, the double confirm living in the UI; everything else runs, with out-of-workspace
writes granted in the ledger.

`DANGER_PATTERNS` triggers no ask of its own; danger names go to audit and display only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .action import OPERATION_WRITE, Action, brokerize

# Verdicts the engine returns.
VERDICT_ALLOW = "allow"
VERDICT_DENY = "deny"

# Normalized outcomes; verdict and kind are kept for existing event and audit readers.
SAFE_AUTO = "SAFE_AUTO"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
POLICY_DENIED = "POLICY_DENIED"

# Kinds record why a call was reviewed or refused, and they select the wording further down.
KIND_CREDENTIAL = "credential"
KIND_DAMAGE = "danger"

CREDENTIAL_MESSAGE = (
    "Permission denied. 原因：目标是受保护的宿主资源（{reason}）。"
    "宿主安全策略不允许读写它，任何确认都无效；请改用别的方式完成任务。"
)
DANGER_MESSAGE = (
    "Permission denied. 原因：毁灭级命令未获确认（{reason}）。"
    "不要重复提交同一条命令；请改用非破坏性做法，或说明你需要它做什么。"
)
UNANSWERED_MESSAGE = (
    "Permission denied. 原因：毁灭级命令（{reason}）需要用户确认，但本次运行没有可用的询问通道。"
    "不要重复提交同一调用；由用户在交互界面亲自确认后重试。"
)

# Approval callback: tool name, arguments and reason; True grants the call for the rest of the run.
AskUser = Callable[[str, dict[str, Any], str], bool]


class Ledger(Protocol):
    """Minimal capability-ledger interface, declared here because the facade imports this module."""

    def knows(self, key: tuple[str, ...]) -> bool: ...

    def remember(self, key: tuple[str, ...]) -> None: ...


@dataclass(frozen=True)
class Decision:
    """Result of one decision: verdict for machines, reason for humans, message for the model."""

    verdict: str
    kind: str = ""
    reason: str = ""
    message: str = ""
    key: tuple[str, ...] | None = None
    answered_by: str = ""
    #: Path grants this decision carries (path -> ro/rw), used by the sandbox when building argv.
    grants: tuple[tuple[str, str], ...] = ()

    @property
    def allowed(self) -> bool:
        return self.verdict == VERDICT_ALLOW

    @property
    def type(self) -> str:
        """Normalized outcome for callers: allow, needs approval, or policy denial."""
        if self.verdict == VERDICT_ALLOW:
            return SAFE_AUTO
        if self.answered_by == "user":
            return NEEDS_APPROVAL
        return POLICY_DENIED

    @property
    def command_type(self) -> str:
        """Explicit alias of ``type`` so callers do not confuse it with the builtin name."""
        return self.type

    def __bool__(self) -> bool:  # existing callers test bool(decision)
        return self.allowed


def review_key(action: Action) -> tuple[str, ...]:
    """Returns the ledger key: normalized bash command, else the first target.

    It is the granularity of "agree once"; MCP tools key on the full name, not on their arguments.
    """
    from .action import is_mcp_tool

    if is_mcp_tool(action.tool):
        return ("tool", action.tool)
    if action.tool == "bash" and action.normalized:
        return ("command", action.normalized)
    if action.targets:
        return ("path", action.targets[0])
    path = action.arguments.get("path")
    if isinstance(path, str) and path.strip():
        return ("path", path.strip())
    return ("tool", action.tool)


def _allow(
    action: Action,
    *,
    answered_by: str,
    kind: str = "",
    reason: str = "",
    key: tuple[str, ...] | None = None,
    ledger: Ledger | None = None,
) -> Decision:
    """Allow one call and record its out-of-workspace path grants so the sandbox mounts them — every
    allow is recorded, catastrophic ones included, and an allow carries no message; the reason still
    goes to the audit.
    """
    access = "rw" if action.operations[:1] == (OPERATION_WRITE,) else "ro"
    grants = tuple((target, access) for target in action.outside_writes)
    if ledger is not None:
        for target, granted in grants:
            ledger.remember(("path", target, granted))
    return Decision(
        VERDICT_ALLOW, kind, reason, "", key=key, answered_by=answered_by, grants=grants
    )


def decide(
    action: Action,
    *,
    full: bool = False,
    ledger: Ledger | None = None,
    ask: AskUser | None = None,
) -> Decision:
    """Single decision entry point; every argument is a fact and only ``ask`` may block on IO."""
    # Step 1, credentials: the only hard refusal (secrets in context cannot be recalled).
    if action.credentials:
        reason = "、".join(action.credentials)
        return Decision(
            VERDICT_DENY,
            KIND_CREDENTIAL,
            reason,
            CREDENTIAL_MESSAGE.format(reason=reason),
        )

    key = review_key(action)

    # Catastrophic commands: asked once here; the double confirm lives in the UI.
    if action.damage:
        reason = action.damage
        if full:
            return _allow(
                action, answered_by="full", kind=KIND_DAMAGE, reason=reason, ledger=ledger
            )
        if ledger is not None and ledger.knows(key):
            return _allow(
                action,
                answered_by="ledger",
                kind=KIND_DAMAGE,
                reason=reason,
                key=key,
                ledger=ledger,
            )
        if ask is None:
            return Decision(
                VERDICT_DENY,
                KIND_DAMAGE,
                reason,
                UNANSWERED_MESSAGE.format(reason=reason),
                key=key,
                answered_by="policy",
            )
        if ask(action.tool, action.arguments, reason):
            if ledger is not None:
                ledger.remember(key)
            return _allow(
                action,
                answered_by="user",
                kind=KIND_DAMAGE,
                reason=reason,
                key=key,
                ledger=ledger,
            )
        return Decision(
            VERDICT_DENY,
            KIND_DAMAGE,
            reason,
            DANGER_MESSAGE.format(reason=reason),
            key=key,
            answered_by="user",
        )

    # Step 3, everything else runs; out-of-workspace writes are granted on the way through.
    return _allow(action, answered_by="policy", ledger=ledger)


def gate(
    name: str,
    arguments: dict[str, Any],
    *,
    full: bool = False,
    ask: AskUser | None = None,
    ledger: Ledger | None = None,
    root: str | None = None,
    action: Action | None = None,
) -> Decision:
    """Composes brokerize and decide; the single entry tools and routes go through."""
    built = action if action is not None else brokerize(name, arguments, root=root)
    return decide(built, full=full, ledger=ledger, ask=ask)


__all__ = [
    "AskUser",
    "CREDENTIAL_MESSAGE",
    "DANGER_MESSAGE",
    "Decision",
    "KIND_CREDENTIAL",
    "KIND_DAMAGE",
    "Ledger",
    "NEEDS_APPROVAL",
    "POLICY_DENIED",
    "SAFE_AUTO",
    "UNANSWERED_MESSAGE",
    "VERDICT_ALLOW",
    "VERDICT_DENY",
    "decide",
    "gate",
    "review_key",
]
