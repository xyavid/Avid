"""Deterministic risk classifier answering auto mode's reviews; a guardrail, not the boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .action import Action, exceeds_sandbox, is_mcp_tool
from .rules import VERDICT_ASK, VERDICT_DENY, Rule  # noqa: F401  (re-exported for type readers)
from .sandbox import SandboxSpec

# Verdict names for callers that log or branch on a review outcome.
ALLOW = "allow"
DENY = "deny"


@dataclass(frozen=True)
class Review:
    """A review verdict; when ``allowed`` is false, ``reason`` must explain it to the model."""

    allowed: bool
    reason: str = ""


def classify(
    action: Action,
    *,
    rule: Rule | None = None,
    sandbox: SandboxSpec | None = None,
) -> Review:
    """Deterministic review of one action; ``rule`` is the ladder hit already found, if any."""
    # A deny-tier hit is refused outright; an ask-tier hit has nobody to answer it without a human.
    if rule is not None and rule.verdict == VERDICT_DENY:
        return Review(False, f"命中 {rule.tier} deny：{rule.reason}")
    if rule is not None and rule.verdict == VERDICT_ASK:
        return Review(False, f"该目标需要逐次批准（{rule.reason}），auto 下无人可答")

    if is_mcp_tool(action.tool):
        # External MCP semantics are opaque to a static checker, so "refuse when unsure" is literal.
        return Review(False, "外部 MCP 工具的语义不可静态判定，auto 下判不准即拒")

    beyond = exceeds_sandbox(action)
    if beyond is not None:
        capability, target = beyond
        return Review(False, f"需要写沙箱保证之外的目标：{target}（{capability}）")

    # The outside-workspace marker is dropped because exceeds_sandbox ruled on that case above.
    risks = [risk for risk in action.risks if risk != "越界"]
    if risks:
        return Review(False, f"命中危险类别：{'、'.join(risks)}")

    # Without a usable sandbox, a state-changing call is refused instead of auto-approved.
    if sandbox is not None and sandbox.degraded and action.operations:
        return Review(False, f"沙箱不可用（{sandbox.reason}）且这次动作会改动状态")

    return Review(True)


__all__ = ["ALLOW", "DENY", "Review", "classify"]
