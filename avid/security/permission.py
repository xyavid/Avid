"""Policy facade: the single point that builds run security, exposes the verdict gate and holds the ledger."""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .action import (
    APPROVAL_RULES,
    COST_RULES,
    DANGER_PATTERNS,
    DENY_PATTERNS,
    OPERATION_READ,
    OPERATION_WRITE,
    Action,
    brokerize,
    command_key,
    danger_categories,
    danger_reason,
    hard_deny,
    normalize_command,
    sensitive_reason,
)
from .audit import AuditLog, default_audit_dir
from .classifier import Review, classify
from .engine import (
    AUTO_UNANSWERED_MESSAGE,
    CREDENTIAL_MESSAGE,
    DANGER_MESSAGE,
    DEGRADED_MESSAGE,
    HARD_MESSAGE,
    KIND_COST,
    KIND_CREDENTIAL,
    KIND_DANGER,
    KIND_DEGRADED,
    KIND_HARD,
    KIND_OUTSIDE,
    KIND_RULE,
    MESSAGE_FOR,
    NEEDS_APPROVAL,
    OUTSIDE_MESSAGE,
    POLICY_DENIED,
    RULE_MESSAGE,
    SAFE_AUTO,
    SANDBOX_DENIED,
    USER_MESSAGE,
    VERDICT_ALLOW,
    VERDICT_ASK,
    VERDICT_DENY,
    AskUser,
    Decision,
    decide,
    gate,
)
from .modes import (
    ADMIN_WRITABLE_SOURCES,
    APPROVAL_CLASSIFIER,
    APPROVAL_NONE,
    APPROVAL_USER,
    APPROVALS,
    DEFAULT_MODE,
    FULL_ACK_HINT,
    FULL_MODE,
    LEGACY_MODES,
    MODE_AUTO,
    MODE_FULL,
    MODE_LABELS,
    MODE_MANUAL,
    MODE_TABLE,
    MODES,
    NETWORK_OPEN,
    NETWORK_RESTRICTED,
    NETWORKS,
    SANDBOX_DISABLED,
    SANDBOX_WORKSPACE,
    SANDBOXES,
    FullAccessError,
    Mode,
    PermissionModeError,
    full_grant_error,
    migrate_mode,
    mode_spec,
    resolve_axes,
    validate_mode,
)
from .rules import (
    TIER_ADMIN,
    TIER_PROJECT,
    TIER_SYSTEM,
    TIER_USER,
    TIERS,
    Ladder,
    PolicyConfigError,
    Rule,
)
from .rules import (
    VERDICT_ASK as RULE_VERDICT_ASK,
)
from .rules import (
    VERDICT_DENY as RULE_VERDICT_DENY,
)
from .sandbox import (
    BACKEND_BWRAP,
    BACKEND_NONE,
    BackendProbe,
    SandboxSpec,
    build_spec,
    default_backend_summary,
    landlock_abi,
    probe_backend,
)

logger = logging.getLogger("avid.security.permission")

# Fallback text for the tool layer when a call arrives without a granted capability.
OUTSIDE_TOOLS_ERROR = "拒绝访问工作区外的路径："

# Concurrent subagent approvals share one terminal, so prompts serialize on this lock.
# The blanket-approval switch travels through RunState explicitly, since contextvars do not cross threads.
_ASK_LOCK = threading.Lock()


class ApprovalLedger:
    """Capabilities approved once for this run, keyed by capability kind and safe across threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: set[tuple[str, ...]] = set()

    def remember(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._keys.add(tuple(key))

    def knows(self, key: tuple[str, ...]) -> bool:
        with self._lock:
            return tuple(key) in self._keys

    def has_capability(self, kind: str, value: str) -> bool:
        """Whether any key of that kind matches the value, ignoring the trailing access segment."""
        with self._lock:
            return any(
                len(key) >= 2 and key[0] == kind and key[1] == value for key in self._keys
            )

    def path_grants(self) -> tuple[tuple[str, str], ...]:
        """Return the approved out-of-workspace paths and access for sandbox argv assembly."""
        with self._lock:
            found: dict[str, str] = {}
            for key in self._keys:
                if len(key) >= 2 and key[0] == "path":
                    access = key[2] if len(key) >= 3 else "rw"
                    if found.get(key[1]) != "rw":
                        found[key[1]] = access
            return tuple(sorted(found.items()))

    def outside_allowed(self, path: object, access: str = "ro") -> bool:
        """Answer a file tool's path check; a read-only grant never upgrades to write."""
        target = str(path)
        if access == "rw":
            return self.knows(("path", target, "rw"))
        return self.knows(("path", target, "ro")) or self.knows(("path", target, "rw"))

    def __len__(self) -> int:  # exposed for tests and diagnostics
        with self._lock:
            return len(self._keys)


@dataclass(frozen=True)
class RunSecurity:
    """The complete security facts for one run: axes, ladder, sandbox and audit log."""

    mode: str
    approval: str
    sandbox_policy: str
    network: str
    sandbox: SandboxSpec
    ladder: Ladder
    audit: AuditLog
    full_granted: bool = False
    notes: tuple[str, ...] = ()

    @property
    def axes(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "approval": self.approval,
            "sandbox": self.sandbox_policy,
            "network": self.network,
        }

    def summary(self) -> dict[str, Any]:
        """Return the shape reported into the run's start event and the audit snapshot."""
        return {
            "mode": self.mode,
            "approval": self.approval,
            "sandbox": self.sandbox_policy,
            "network": self.network,
            "full_granted": self.full_granted,
            "sandbox_state": self.sandbox.summary(),
            "rules": len(self.ladder.rules),
            "notes": [*self.notes, *self.ladder.notes],
        }

    def audit_write(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        return self.audit.write(kind, **fields)


def _default_root() -> str:
    """Resolve the process workspace root: the one place the ladder and the sandbox agree on.

    The lazy import avoids a package cycle, since the tools package imports this module in turn.
    """
    from ..agent.tools import workspace

    return str(workspace.WORKSPACE_ROOT)


def build_run_security(
    *,
    mode: str = DEFAULT_MODE,
    root: str | None = None,
    home: str | Path | None = None,
    run_tag: str = "",
    run_id: str = "",
    full_ack: bool = False,
    source: str = "cli",
    system_policy: str | Path | None = None,
    project_policy: str | Path | None = None,
    probe: BackendProbe | None = None,
    audit_dir: str | Path | None = None,
    audit_enabled: bool = True,
) -> RunSecurity:
    """Assemble run security from the three axes, the ladder, the sandbox spec and the audit sink."""
    # Full access must be acknowledged explicitly; an unacknowledged request is refused, not downgraded.
    problem = full_grant_error(mode, acknowledged=full_ack, source=source)
    if problem is not None:
        raise FullAccessError(problem)
    name = validate_mode(mode)
    spec = mode_spec(name)
    resolved = root if root is not None else _default_root()
    ladder = Ladder.load(
        root=resolved, home=home, system_path=system_policy, project_path=project_policy
    )
    sandbox = build_spec(
        policy=spec.sandbox, network=spec.network, root=resolved, home=home, probe=probe
    )
    directory = None
    if audit_enabled:
        directory = (
            Path(audit_dir) if audit_dir is not None else default_audit_dir(home)
        )
    audit = AuditLog(
        directory=directory,
        run_tag=run_tag,
        run_id=run_id,
        mode=name,
        axes={
            "approval": spec.approval,
            "sandbox": spec.sandbox,
            "network": spec.network,
        },
        sandbox=sandbox.summary(),
    )
    return RunSecurity(
        mode=name,
        approval=spec.approval,
        sandbox_policy=spec.sandbox,
        network=spec.network,
        sandbox=sandbox,
        ladder=ladder,
        audit=audit,
        full_granted=name == FULL_MODE,
        notes=tuple(ladder.notes),
    )


def ask_user(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """Ask for interactive confirmation on stderr, treating unreadable input as a refusal.

    The prompt goes to stderr so it cannot pollute the answer printed on stdout.
    """
    detail = json.dumps(arguments, ensure_ascii=False, default=str)
    # Serialize prompts: parallel subagents ask at once but only one terminal exists.
    with _ASK_LOCK:
        print(
            f"\n⚠ 需要确认：{reason}\n  工具 {name} {detail}\n  允许执行？[y/N] ",
            file=sys.stderr,
            end="",
            flush=True,
        )
        try:
            answer = sys.stdin.readline()
        except (OSError, KeyboardInterrupt):
            return False

    if not answer:
        print("（读不到输入，视为拒绝）", file=sys.stderr)
        return False
    return answer.strip().lower() in {"y", "yes"}


def _always_allow(name: str, arguments: dict[str, Any], reason: str) -> bool:
    return True


# Public responder for --yes: answers yes to every ask while the engine still blocks hard denials.
always_allow = _always_allow


def check_permission(
    name: str,
    arguments: dict[str, Any],
    *,
    ask: AskUser | None = None,
    mode: str = DEFAULT_MODE,
    ledger: ApprovalLedger | None = None,
    ladder: Ladder | None = None,
    sandbox: SandboxSpec | None = None,
    root: str | None = None,
    danger: str | None = None,
    outside: str | None = None,
) -> bool:
    """Decide one call and return a plain bool, wrapping the gate for existing callers and tests."""
    return gate(
        name,
        arguments,
        mode=mode,
        ask=ask,
        ledger=ledger,
        ladder=ladder,
        sandbox=sandbox,
        root=root,
        danger=danger,
        outside=outside,
    ).allowed


def auto_approve(
    name: str,
    arguments: dict[str, Any],
    *,
    mode: str = DEFAULT_MODE,
    ledger: ApprovalLedger | None = None,
    ladder: Ladder | None = None,
    sandbox: SandboxSpec | None = None,
    root: str | None = None,
    danger: str | None = None,
    outside: str | None = None,
) -> bool:
    """Answer every approval for this run with yes, without disabling the sandbox or any denial rule.

    Only the responder changes; which actions still raise a question stays with the three axes.
    """
    return check_permission(
        name,
        arguments,
        ask=_always_allow,
        mode=mode,
        ledger=ledger,
        ladder=ladder,
        sandbox=sandbox,
        root=root,
        danger=danger,
        outside=outside,
    )


__all__ = [
    "ADMIN_WRITABLE_SOURCES",
    "APPROVAL_CLASSIFIER",
    "APPROVAL_NONE",
    "APPROVAL_RULES",
    "APPROVAL_USER",
    "APPROVALS",
    "Action",
    "ApprovalLedger",
    "AuditLog",
    "BACKEND_BWRAP",
    "BACKEND_NONE",
    "BackendProbe",
    "AUTO_UNANSWERED_MESSAGE",
    "COST_RULES",
    "CREDENTIAL_MESSAGE",
    "DANGER_MESSAGE",
    "DANGER_PATTERNS",
    "DEFAULT_MODE",
    "DEGRADED_MESSAGE",
    "DENY_PATTERNS",
    "Decision",
    "FULL_ACK_HINT",
    "FULL_MODE",
    "FullAccessError",
    "HARD_MESSAGE",
    "KIND_COST",
    "KIND_CREDENTIAL",
    "KIND_DANGER",
    "KIND_DEGRADED",
    "KIND_HARD",
    "KIND_OUTSIDE",
    "KIND_RULE",
    "LEGACY_MODES",
    "Ladder",
    "MESSAGE_FOR",
    "MODE_AUTO",
    "MODE_FULL",
    "MODE_LABELS",
    "MODE_MANUAL",
    "MODE_TABLE",
    "MODES",
    "Mode",
    "NETWORKS",
    "NETWORK_OPEN",
    "NETWORK_RESTRICTED",
    "OPERATION_READ",
    "OPERATION_WRITE",
    "NEEDS_APPROVAL",
    "OUTSIDE_MESSAGE",
    "POLICY_DENIED",
    "SAFE_AUTO",
    "SANDBOX_DENIED",
    "OUTSIDE_TOOLS_ERROR",
    "PermissionModeError",
    "PolicyConfigError",
    "RULE_MESSAGE",
    "RULE_VERDICT_ASK",
    "RULE_VERDICT_DENY",
    "Review",
    "Rule",
    "RunSecurity",
    "SANDBOXES",
    "SANDBOX_DISABLED",
    "SANDBOX_WORKSPACE",
    "SandboxSpec",
    "TIERS",
    "TIER_ADMIN",
    "TIER_PROJECT",
    "TIER_SYSTEM",
    "TIER_USER",
    "USER_MESSAGE",
    "VERDICT_ALLOW",
    "VERDICT_ASK",
    "VERDICT_DENY",
    "always_allow",
    "auto_approve",
    "brokerize",
    "build_run_security",
    "build_spec",
    "check_permission",
    "classify",
    "command_key",
    "danger_categories",
    "danger_reason",
    "decide",
    "default_audit_dir",
    "default_backend_summary",
    "full_grant_error",
    "gate",
    "hard_deny",
    "landlock_abi",
    "migrate_mode",
    "mode_spec",
    "normalize_command",
    "probe_backend",
    "resolve_axes",
    "sensitive_reason",
    "validate_mode",
]
