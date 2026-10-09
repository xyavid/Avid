"""Policy facade: builds run security, exposes the verdict gate and holds the ledger.

阶段 51 轻量化后的形态：没有模式阶梯，只有「默认直接跑 + 毁灭级双确认 +
凭据拒读」；full 是唯一保留的显式授权形态（跳过双确认、关沙箱、不滤 env）。
「完全访问」由调用方显式给出（CLI --allow-full-access / Web full_access_ack），
没有请求-降级路径：给不出授权就没有 full。
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

# 记录口径的全部取值：normal = 默认（毁灭级双确认）；full = 完全访问（显式授权）。
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
        """Wire/记录用两值口径：normal（默认直接跑）或 full（完全访问）。"""
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


#: 提问通道的形状：问题 + 可选项（空 = 自由文本）→ 答案；None 表示没人作答。
#: 放在 security 层只是因为 AskUser 在这里——它本身不含任何策略。
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
    """Assemble run security: the sandbox spec and the audit sink; full 只是个开关。

    ``read_only`` 是临时对话（阶段 54）：工作区在沙箱里只读，连 bash 也改不动文件。
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
    """毁灭级命令的终端确认：连问两次（二次确认），任何一次拒绝都不执行。

    The prompts go to stderr so they cannot pollute the answer printed on stdout.
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
