"""Turns one Action into one verdict: deny outranks ask, and ask outranks allow, in every mode."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .action import (
    APPROVAL_RULES,
    COST_RULES,
    OPERATION_WRITE,
    Action,
    brokerize,
    exceeds_sandbox,
    is_mcp_tool,
    mcp_server,
)
from .classifier import classify
from .modes import (
    APPROVAL_CLASSIFIER,
    APPROVAL_NONE,
    APPROVAL_USER,
    DEFAULT_MODE,
    NETWORK_RESTRICTED,
    Mode,
    mode_spec,
)
from .rules import (
    OPERATIONS,
    TIER_ADMIN,
    TIER_ORDER,
    Ladder,
    Rule,
)

# The ladder's deny string equals the engine's, but each module is authoritative, so it is aliased
# explicitly instead of being shared under one name.
from .rules import VERDICT_DENY as RULE_DENY
from .sandbox import UNMANAGED, SandboxSpec

# Verdicts the engine returns.
VERDICT_ALLOW = "allow"
VERDICT_ASK = "ask"
VERDICT_DENY = "deny"

# Normalized outcomes; verdict and kind are kept for existing event and audit readers.
SAFE_AUTO = "SAFE_AUTO"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
SANDBOX_DENIED = "SANDBOX_DENIED"
POLICY_DENIED = "POLICY_DENIED"

# Kinds record why a call was reviewed or refused, and they select the wording further down.
KIND_HARD = "hard"
KIND_CREDENTIAL = "credential"
KIND_RULE = "rule"
KIND_OUTSIDE = "outside"
KIND_DANGER = "danger"
KIND_DEGRADED = "degraded"
KIND_NETWORK = "network"
KIND_NET_ASK = "net_ask"
KIND_COST = "cost"
#: External MCP tools: unclassifiable semantics, so manual asks, auto refuses and full allows.
KIND_MCP = "mcp"

# Messages for the model; each denial kind points at a different next step, because "never allowed"
# and "not this time" mean different things to a model and mixing them invites blind retries.
HARD_MESSAGE = (
    "Permission denied. 原因：硬拒绝（{reason}）。"
    "这条命令被永久禁止，不要重试、也不要改写绕过，请改用别的方式完成任务。"
)
CREDENTIAL_MESSAGE = (
    "Permission denied. 原因：目标是受保护的宿主资源（{reason}）。"
    "宿主安全策略不允许读写它，任何模式与任何批准都无效；请改用别的方式完成任务。"
)
RULE_MESSAGE = (
    "Permission denied. 原因：命中安全策略（{reason}）。"
    "这条规则由宿主或仓库配置声明，本会话的批准无法覆盖它；请改用别的方式完成任务。"
)
DANGER_MESSAGE = (
    "Permission denied. 原因：危险命令未获批准（{reason}）。"
    "不要重复提交同一条命令；请改用非破坏性做法，或说明你需要它做什么。"
)
OUTSIDE_MESSAGE = (
    "Permission denied. 原因：要写沙箱保证之外的目标且未获批准（{reason}）。"
    "读文件不受影响；请改到工作区内完成，或让用户批准这条路径后再写。"
    "不要重复尝试同一目标。"
)
DEGRADED_MESSAGE = (
    "Permission denied. 原因：沙箱不可用（{reason}），且这次动作证明不了只落在工作区内。"
    "请让用户批准这一条，或改用确定性更高的做法；只读与工作区内写不受影响。"
)
NETWORK_MESSAGE = (
    "SANDBOX_NETWORK_DENIED: network_connect 被网络沙箱禁止（{reason}）。"
    "请勿重试同一网络请求；由用户显式调整网络能力或使用已授权的工具。"
)
NET_ASK_MESSAGE = (
    "Permission denied. 原因：网络类命令需要批准（{reason}）。"
    "沙箱缺席时网络出口由人把守；请向用户说明要访问哪个地址、为什么，"
    "或改用已授权的工具。"
)
AUTO_UNANSWERED_MESSAGE = (
    "Permission denied. 原因：auto 模式需要就「{reason}」征询用户，但本次运行没有可用的询问通道。"
    "请让用户改用 manual 模式逐条批准，或调整权限模式后重试；不要重复提交同一调用。"
)
USER_MESSAGE = (
    "Permission denied. 原因：本次未获用户批准。"
    "不要重复提交同一条调用；请说明你需要它做什么，或改用其它工具。"
)
MCP_MESSAGE = (
    "Permission denied. 原因：外部 MCP 工具（{reason}）未获批准。"
    "它的行为只有 server 自己知道，需要用户逐个工具批准；"
    "请向用户说明要调用哪个工具、为什么，或改用内置工具完成任务。"
)

MESSAGE_FOR: dict[str, str] = {
    KIND_HARD: HARD_MESSAGE,
    KIND_CREDENTIAL: CREDENTIAL_MESSAGE,
    KIND_RULE: RULE_MESSAGE,
    KIND_DANGER: DANGER_MESSAGE,
    KIND_OUTSIDE: OUTSIDE_MESSAGE,
    KIND_DEGRADED: DEGRADED_MESSAGE,
    KIND_NETWORK: NETWORK_MESSAGE,
    KIND_NET_ASK: NET_ASK_MESSAGE,
    KIND_COST: USER_MESSAGE,
    KIND_MCP: MCP_MESSAGE,
}

# Approval callback: tool name, arguments and reason; True grants the call for the rest of the run.
AskUser = Callable[[str, dict[str, Any], str], bool]


class Ledger(Protocol):
    """Minimal capability-ledger interface, declared here because the facade imports this module."""

    def knows(self, key: tuple[str, ...]) -> bool: ...

    def has_capability(self, kind: str, value: str) -> bool: ...

    def remember(self, key: tuple[str, ...]) -> None: ...


@dataclass(frozen=True)
class Decision:
    """Result of one decision: verdict for machines, reason for humans, message for the model."""

    verdict: str
    kind: str = ""
    tier: str = ""
    reason: str = ""
    message: str = ""
    key: tuple[str, ...] | None = None
    answered_by: str = ""
    #: Capabilities granted by an approval (path -> ro/rw), used by the sandbox when building argv.
    grants: tuple[tuple[str, str], ...] = ()
    code: str = ""
    operation: str = ""
    target: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == VERDICT_ALLOW

    @property
    def type(self) -> str:
        """Normalized outcome for callers: allow, sandbox denial, needs approval, or policy denial.

        A degraded sandbox is a boundary failure, not an ordinary policy refusal.
        """
        if self.verdict == VERDICT_ALLOW:
            return SAFE_AUTO
        if self.code.startswith("SANDBOX_") or self.kind in {KIND_DEGRADED, KIND_NETWORK}:
            return SANDBOX_DENIED
        if self.answered_by == "user":
            return NEEDS_APPROVAL
        return POLICY_DENIED

    @property
    def command_type(self) -> str:
        """Explicit alias of ``type`` so callers do not confuse it with the builtin name."""
        return self.type

    def __bool__(self) -> bool:  # existing callers test bool(decision)
        return self.allowed


def access_for(action: Action) -> str:
    """Reports how a granted capability is mounted: read-write for writes, read-only otherwise."""
    return "rw" if action.operations[:1] == (OPERATION_WRITE,) else "ro"


def ladder_hit(action: Action, ladder: Ladder | None) -> Rule | None:
    """Returns the highest-priority ladder hit across every target of the action.

    A bash call can carry several targets, so each one is checked instead of trusting the first.
    """
    if ladder is None:
        return None
    operations = action.operations or OPERATIONS
    hits: list[Rule] = []
    for target in action.targets:
        rule = ladder.verdict_for(target, operations)
        if rule is not None:
            hits.append(rule)
    if not hits:
        return None
    # Deny outranks ask first, and only then does the higher tier win.
    hits.sort(key=lambda rule: (rule.verdict != RULE_DENY, TIER_ORDER[rule.tier]))
    return hits[0]


def review_key(action: Action) -> tuple[str, ...]:
    """Returns the review ledger key: normalized bash command, else the first target.

    It is the granularity of "agree once"; MCP tools key on the full name, not on their arguments.
    """
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


def review_facts(
    action: Action, rule: Rule | None, sandbox: SandboxSpec | None
) -> tuple[str, str, str] | None:
    """Returns ``(kind, reason, detail)`` when this step needs review, else ``None``.

    ``detail`` stays separate so the denial message does not nest one full reason inside another.
    """
    if rule is not None:
        kind = KIND_CREDENTIAL if rule.tier == TIER_ADMIN else KIND_RULE
        prefix = "受保护的宿主资源" if rule.tier == TIER_ADMIN else "安全策略要求逐次批准"
        return kind, f"{prefix}（{rule.reason}）", rule.reason

    if is_mcp_tool(action.tool):
        # An external MCP tool always goes to review: manual asks once, auto has nobody to ask.
        server = mcp_server(action.tool)
        return KIND_MCP, f"外部 MCP 工具（{server} server）", action.tool

    beyond = exceeds_sandbox(action)
    if beyond is not None:
        capability, target = beyond
        return KIND_OUTSIDE, f"需要写沙箱保证之外的 {target}（{capability}）", target

    risks = [risk for risk in action.risks if risk != "越界"]
    if risks:
        joined = "、".join(risks)
        return KIND_DANGER, joined, joined

    # Without an enforcing sandbox the network boundary is held by a human, so a network
    # command becomes a review instead of running silently. The enforcing case is denied
    # physically before this point, and full mode runs with network=open and never lands here.
    if action.network and sandbox is not None and sandbox.network == NETWORK_RESTRICTED:
        target = action.network_target or "unknown"
        return KIND_NET_ASK, f"网络出口（{target}）", target

    if sandbox is not None and sandbox.degraded and action.tool in APPROVAL_RULES:
        # Only managed tools come back to review: an in-workspace read never needed approval anyway.
        detail = sandbox.reason or "后端不可用"
        return KIND_DEGRADED, f"沙箱不可用（{detail}）", detail

    if action.tool in COST_RULES:
        return KIND_COST, COST_RULES[action.tool], COST_RULES[action.tool]

    return None


def _grant_key(action: Action) -> tuple[tuple[str, str], ...]:
    access = access_for(action)
    return tuple((target, access) for target in action.outside_writes)


def _message(kind: str, reason: str) -> str:
    return MESSAGE_FOR.get(kind, USER_MESSAGE).format(reason=reason)


def decide(
    action: Action,
    *,
    mode: str | Mode = DEFAULT_MODE,
    ladder: Ladder | None = None,
    sandbox: SandboxSpec | None = None,
    ledger: Ledger | None = None,
    ask: AskUser | None = None,
) -> Decision:
    """Single decision entry point; every argument is a fact and only ``ask`` may block on IO."""
    spec = mode_spec(mode) if isinstance(mode, str) else mode
    approval = spec.approval
    # A missing sandbox spec means one was requested with no backend, so fail closed.
    sandbox = sandbox if sandbox is not None else UNMANAGED

    # Step 1, hard deny: terminal in every mode, and no answer from anyone can override it.
    if action.damage:
        return Decision(
            VERDICT_DENY,
            KIND_HARD,
            TIER_ADMIN,
            action.damage,
            HARD_MESSAGE.format(reason=action.damage),
        )

    # Step 2, ladder: a deny hit is final even in full, while an ask hit goes to review.
    rule = ladder_hit(action, ladder)
    if rule is not None and rule.verdict == RULE_DENY:
        kind = KIND_CREDENTIAL if rule.tier == TIER_ADMIN else KIND_RULE
        reason = f"{rule.reason}（{rule.tier}）"
        return Decision(VERDICT_DENY, kind, rule.tier, reason, _message(kind, rule.reason))

    # The sandbox mounts only paths that already exist, so an outside target that is missing is
    # refused here rather than surfacing as a read-only error after an approval was granted.
    if sandbox.enforced:
        beyond = exceeds_sandbox(action)
        if beyond is not None and action.tool == "bash":
            capability, target = beyond
            if not Path(target).exists():
                return Decision(
                    VERDICT_DENY,
                    KIND_OUTSIDE,
                    "",
                    f"沙箱无法挂载尚不存在的区外目标 {target}",
                    f"SANDBOX_FILESYSTEM_DENIED: {capability} 需要工作区之外已存在的目标"
                    f"（{target}）；请先在工作区内完成，或让用户把它加进可写根。",
                    code="SANDBOX_FILESYSTEM_DENIED",
                    operation=capability,
                    target=target,
                )

    # Resource limits are orthogonal to approval, so a restricted network is refused up front
    # instead of surfacing later as a DNS error from the command itself. The gate needs the
    # sandbox to be enforced: without a backend the network boundary moves to a human instead.
    if action.network and sandbox.enforced and sandbox.network == NETWORK_RESTRICTED:
        operation = "network_listen" if "network_listen" in action.capabilities else "network_connect"
        target = action.network_target or "unknown"
        code = "SANDBOX_NETWORK_DENIED"
        return Decision(
            VERDICT_DENY, KIND_NETWORK, "", f"网络沙箱禁止 {operation} {target}",
            NETWORK_MESSAGE.format(reason=target),
            code=code, operation=operation, target=target,
        )

    facts = review_facts(action, rule, sandbox)
    if facts is None:
        return Decision(VERDICT_ALLOW, answered_by="policy")

    kind, reason, detail = facts
    key = review_key(action)
    grants = _grant_key(action)

    # Step 3, full: nothing is asked, and the audit still records that nobody stopped it.
    if approval == APPROVAL_NONE:
        return Decision(VERDICT_ALLOW, kind, "", reason, answered_by="none", key=key)

    # Step 4, the ledger: one approval of a capability is enough for the rest of the run.
    path_access = access_for(action)
    paths_granted = bool(action.outside_writes) and all(
        ledger.knows(("path", target, path_access))
        or (path_access == "ro" and ledger.knows(("path", target, "rw")))
        for target in action.outside_writes
    ) if ledger is not None else False
    if ledger is not None and (ledger.knows(key) or paths_granted):
        return Decision(VERDICT_ALLOW, kind, "", reason, key=key, answered_by="ledger")

    # Step 5, classifier: prove the call safe, else hand it to a human. Refusal happens
    # only when nobody can answer — a reachable human outranks the classifier's doubt.
    if approval == APPROVAL_CLASSIFIER:
        review = classify(action, rule=rule, sandbox=sandbox)
        if review.allowed:
            return Decision(VERDICT_ALLOW, kind, "classifier", reason, answered_by="classifier")
        if ask is None:
            return Decision(
                VERDICT_DENY,
                kind,
                "classifier",
                reason,
                AUTO_UNANSWERED_MESSAGE.format(reason=review.reason),
                key=key,
                answered_by="classifier",
            )
        # 判不准交人：落到第 6 步，批准与拒绝都记在用户名下。

    # Step 6, approval user: ask a human; a missing answerer, EOF or timeout all end in refusal.
    answerer = ask
    if answerer is not None and answerer(action.tool, action.arguments, reason):
        if ledger is not None:
            ledger.remember(key)
            for target, access in grants:
                ledger.remember(("path", target, access))
        return Decision(
            VERDICT_ALLOW, kind, "", reason, key=key, answered_by="user", grants=grants
        )
    return Decision(
        VERDICT_DENY,
        kind,
        rule.tier if rule is not None else "",
        reason,
        _message(kind, detail),
        key=key,
        answered_by="user",
    )


def gate(
    name: str,
    arguments: dict[str, Any],
    *,
    mode: str | Mode = DEFAULT_MODE,
    ask: AskUser | None = None,
    ledger: Ledger | None = None,
    ladder: Ladder | None = None,
    sandbox: SandboxSpec | None = None,
    root: str | None = None,
    danger: str | None = None,
    outside: str | None = None,
    action: Action | None = None,
) -> Decision:
    """Composes brokerize and decide; danger and outside may come from caller-computed facts.

    A missing ladder is loaded on the spot, since no ladder at all means no deny rules.
    """
    built = action if action is not None else brokerize(name, arguments, root=root)
    if ladder is None:
        ladder = Ladder.load(root=root)
    if danger is not None or outside is not None:
        # A caller-supplied fact is inserted ahead of what the broker derived from arguments.
        risks = list(built.risks)
        if danger and danger not in risks:
            risks.insert(0, danger)
        targets = built.outside
        if outside:
            targets = tuple(dict.fromkeys((*built.outside, str(outside))))
        built = replace(built, risks=tuple(risks), outside=targets)
    return decide(built, mode=mode, ladder=ladder, sandbox=sandbox, ledger=ledger, ask=ask)


__all__ = [
    "AskUser",
    "AUTO_UNANSWERED_MESSAGE",
    "CREDENTIAL_MESSAGE",
    "DANGER_MESSAGE",
    "DEGRADED_MESSAGE",
    "Decision",
    "HARD_MESSAGE",
    "KIND_COST",
    "KIND_CREDENTIAL",
    "KIND_DANGER",
    "KIND_DEGRADED",
    "KIND_HARD",
    "KIND_NET_ASK",
    "KIND_NETWORK",
    "KIND_OUTSIDE",
    "KIND_RULE",
    "Ledger",
    "MESSAGE_FOR",
    "NET_ASK_MESSAGE",
    "NEEDS_APPROVAL",
    "POLICY_DENIED",
    "SAFE_AUTO",
    "SANDBOX_DENIED",
    "USER_MESSAGE",
    "VERDICT_ALLOW",
    "VERDICT_ASK",
    "VERDICT_DENY",
    "access_for",
    "decide",
    "gate",
    "ladder_hit",
    "review_facts",
    "review_key",
    "APPROVAL_USER",
    "OPERATIONS",
]
