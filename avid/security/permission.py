"""Policy facade: builds run security, exposes the verdict gate and holds the ledger.

No mode ladder: everything runs by default, catastrophic commands need a double confirm and
credential reads are refused outright; `full` is the only explicit authorization form (it skips the
double confirm, disables the sandbox and leaves env unfiltered) and must be given by the caller
(CLI --allow-full-access / Web full_access_ack) — there is no request-downgrade path to it.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .action import (
    DANGER_PATTERNS,
    DENY_PATTERNS,
    Action,
    brokerize,
    danger_categories,
    danger_reason,
    sensitive_reason,
)
from .audit import AuditLog, default_audit_dir
from .engine import (
    CREDENTIAL_MESSAGE,
    DANGER_MESSAGE,
    KIND_CREDENTIAL,
    KIND_DAMAGE,
    NEEDS_APPROVAL,
    POLICY_DENIED,
    SAFE_AUTO,
    UNANSWERED_MESSAGE,
    VERDICT_ALLOW,
    VERDICT_DENY,
    AskUser,
    Decision,
    Ledger,
    decide,
    gate,
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

#: Recording vocabulary: normal (default, catastrophic asks) or full (explicit full access).
PERMISSION_NORMAL = "normal"
PERMISSION_FULL = "full"
PERMISSIONS: tuple[str, ...] = (PERMISSION_NORMAL, PERMISSION_FULL)

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

    def __len__(self) -> int:  # exposed for tests and diagnostics
        with self._lock:
            return len(self._keys)


@dataclass(frozen=True)
class RunSecurity:
    """The complete security facts for one run: full flag, sandbox and audit log."""

    full: bool
    sandbox: SandboxSpec
    audit: AuditLog
    notes: tuple[str, ...] = ()

    @property
    def permission_mode(self) -> str:
        """Wire and audit vocabulary in two values: normal or full."""
        return PERMISSION_FULL if self.full else PERMISSION_NORMAL

    def summary(self) -> dict[str, Any]:
        """Return the shape reported into the run's start event and the audit snapshot."""
        return {
            "permission": self.permission_mode,
            "sandbox_state": self.sandbox.summary(),
            "notes": self.notes,
        }

    def audit_write(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        return self.audit.write(kind, **fields)


def _default_root() -> str:
    """Resolve the process workspace root; the lazy import avoids a package cycle."""
    from ..agent.tools import workspace

    return str(workspace.WORKSPACE_ROOT)


#: Ask channel: question + options (empty = free text) -> answer; None = nobody answered.
AskQuestion = Callable[[str, tuple[str, ...]], "str | None"]


def build_run_security(
    *,
    full: bool = False,
    root: str | None = None,
    home: str | Path | None = None,
    run_tag: str = "",
    run_id: str = "",
    probe: BackendProbe | None = None,
    audit_dir: str | Path | None = None,
    audit_enabled: bool = True,
    read_only: bool = False,
) -> RunSecurity:
    """Assemble run security: the sandbox spec and the audit sink (full is just a flag); read_only
    mounts the workspace read-only in the sandbox, so even bash cannot change files.
    """
    resolved = root if root is not None else _default_root()
    sandbox = build_spec(
        policy="disabled" if full else "workspace",
        root=resolved,
        home=home,
        probe=probe,
        read_only=read_only,
    )
    directory = None
    if audit_enabled:
        directory = Path(audit_dir) if audit_dir is not None else default_audit_dir(home)
    audit = AuditLog(
        directory=directory,
        run_tag=run_tag,
        run_id=run_id,
        mode=PERMISSION_FULL if full else PERMISSION_NORMAL,
        axes={"full": full},
        sandbox=sandbox.summary(),
    )
    return RunSecurity(full=full, sandbox=sandbox, audit=audit, notes=())


def ask_user(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """Ask twice for a catastrophic command and run it only if both answers are yes; the prompts go
    to stderr so they never pollute the answer printed on stdout.
    """
    detail = json.dumps(arguments, ensure_ascii=False, default=str)
    # Serialize prompts: parallel subagents ask at once but only one terminal exists.
    with _ASK_LOCK:
        print(
            f"\n⚠ 毁灭级命令：{reason}\n  工具 {name} {detail}\n  允许执行？[y/N] ",
            file=sys.stderr,
            end="",
            flush=True,
        )
        if not _read_yes():
            return False
        print("  ⚠ 再次确认（毁灭级命令）：真的允许执行？[y/N] ", file=sys.stderr, end="", flush=True)
        return _read_yes()


def _read_yes() -> bool:
    try:
        answer = sys.stdin.readline()
    except (OSError, KeyboardInterrupt):
        return False
    if not answer:
        print("（读不到输入，视为拒绝）", file=sys.stderr)
        return False
    return answer.strip().lower() in {"y", "yes"}


def always_allow(name: str, arguments: dict[str, Any], reason: str) -> bool:
    return True


# Public responder for --yes: answers yes to every ask while the engine still refuses credentials.
always_allow_ask: AskUser = always_allow


def check_permission(
    name: str,
    arguments: dict[str, Any],
    *,
    ask: AskUser | None = None,
    full: bool = False,
    ledger: Ledger | None = None,
    root: str | None = None,
    action: Action | None = None,
) -> bool:
    """Decide one call and return a plain bool, wrapping the gate for existing callers and tests."""
    return gate(
        name,
        arguments,
        ask=ask,
        full=full,
        ledger=ledger,
        root=root,
        action=action,
    ).allowed


def auto_approve(
    name: str,
    arguments: dict[str, Any],
    *,
    full: bool = False,
    ledger: Ledger | None = None,
    root: str | None = None,
    action: Action | None = None,
) -> bool:
    """Answer every ask with yes (--yes): the human said so for this run."""
    return check_permission(
        name,
        arguments,
        ask=always_allow_ask,
        full=full,
        ledger=ledger,
        root=root,
        action=action,
    )


__all__ = [
    "Action",
    "ApprovalLedger",
    "AuditLog",
    "BACKEND_BWRAP",
    "BACKEND_NONE",
    "BackendProbe",
    "CREDENTIAL_MESSAGE",
    "DANGER_MESSAGE",
    "DANGER_PATTERNS",
    "DENY_PATTERNS",
    "Decision",
    "KIND_CREDENTIAL",
    "KIND_DAMAGE",
    "NEEDS_APPROVAL",
    "PERMISSIONS",
    "PERMISSION_FULL",
    "PERMISSION_NORMAL",
    "POLICY_DENIED",
    "SAFE_AUTO",
    "SandboxSpec",
    "UNANSWERED_MESSAGE",
    "VERDICT_ALLOW",
    "VERDICT_DENY",
    "RunSecurity",
    "always_allow",
    "ask_user",
    "auto_approve",
    "brokerize",
    "build_run_security",
    "build_spec",
    "check_permission",
    "danger_categories",
    "danger_reason",
    "decide",
    "default_backend_summary",
    "gate",
    "landlock_abi",
    "probe_backend",
    "sensitive_reason",
]
