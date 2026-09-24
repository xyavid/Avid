"""策略包的门面：运行级安全规格 + 单一裁决入口 + 审批回答者。

``runtime/`` 与 ``workspaces.py`` 只认这一个模块，策略内部怎么分层是这里的事：

::

    action.py     Tool Broker   ── 归一化参数、识别目标、分类风险
    rules.py      四级 deny 阶梯 ─ ADMIN / SYSTEM / PROJECT / USER
    sandbox.py    Sandbox Manager ─ bwrap argv、掩蔽、env 白名单、网络命名空间
    classifier.py auto 的确定性审查
    audit.py      审计落盘
    engine.py     Policy Engine ── Action × 阶梯 × 三轴 → Decision
    permission.py 门面（本文件） ── build_run_security / gate / 账本 / 回答者

门面存在的理由不是"好看"，而是三条会失败的约束：

1. ``runtime/`` 对策略层的运行时依赖必须**逐文件可枚举**（``tests/test_web_boundaries.py``
   的 A13 门禁）：门面让"runtime 用到的策略能力"是一个集合，而不是散开的十来个模块；
2. ``loop.py`` 与 ``execution.py`` 必须零策略依赖：门面把所有策略能力聚在一处，越界
   一眼可见；
3. 三轴的解析（模式 → approval/sandbox/network）只能有一个点：:func:`build_run_security`。

**四层裁决**（细节见 :mod:`avid.policy.engine`）：硬拒绝 → 四级 deny 阶梯 → 越界 →
危险 → 降级 → 成本 → 放行；REVIEW 由 ``approval`` 回答（人 / 分类器 / 无人）。

**能力账本**（:class:`ApprovalLedger`）是"同意一次即生效"的落点，键的类型就是能力的
类型：``("command", 原文)`` / ``("path", 绝对路径, ro|rw)`` / ``("tool", 名字)``。
它只活一次运行、只在内存里；想持久化授权就是另一个决策（见 ``docs/design/``）。
"""

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
    CLASSIFIER_MESSAGE,
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

logger = logging.getLogger("avid.policy.permission")

# 工具层没有授权时的兜底文本（gate 没批准、或工具被直接调用时生效）。
OUTSIDE_TOOLS_ERROR = "拒绝访问工作区外的路径："

# 多个 subagent 并行时可能同时来要审批，而终端只有一个。
#
# 免审批开关本身由 RunState 显式传递，不再用 ContextVar：子 agent 在别的线程跑，
# contextvars 不跨线程继承，隐式状态在那里会静默失效；显式传参则传不过去就报错。
_ASK_LOCK = threading.Lock()


class ApprovalLedger:
    """一次运行内的"已同意"能力账本。

    带锁是因为 subagent 在并行线程里跑，且与父 agent 共用同一本账。

    键的**类型**就是能力的类型（原则⑦：升级是授予能力，不是关沙箱）：

    * ``("command", 归一化命令原文)`` —— allow this command
    * ``("path", 绝对路径, "ro"|"rw")``   —— allow this path
    * ``("tool", 工具名)``                —— allow this session（按工具记）
    """

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
        """有没有某类能力（忽略最后一段的口径，如 ro/rw）。"""
        with self._lock:
            return any(
                len(key) >= 2 and key[0] == kind and key[1] == value for key in self._keys
            )

    def path_grants(self) -> tuple[tuple[str, str], ...]:
        """本次运行获准的区外路径（路径, ro/rw）——沙箱组装 argv 时用。"""
        with self._lock:
            found: dict[str, str] = {}
            for key in self._keys:
                if len(key) >= 2 and key[0] == "path":
                    access = key[2] if len(key) >= 3 else "rw"
                    if found.get(key[1]) != "rw":
                        found[key[1]] = access
            return tuple(sorted(found.items()))

    def outside_allowed(self, path: object, access: str = "ro") -> bool:
        """文件工具按访问口径查询路径能力。只读授权永不自动升级为写。"""
        target = str(path)
        if access == "rw":
            return self.knows(("path", target, "rw"))
        return self.knows(("path", target, "ro")) or self.knows(("path", target, "rw"))

    def __len__(self) -> int:  # 便于测试与诊断
        with self._lock:
            return len(self._keys)


@dataclass(frozen=True)
class RunSecurity:
    """一次运行的完整安全规格：三轴 + 阶梯 + 沙箱 + 审计。

    它是"运行级安全事实"的**唯一**载体：``RunState`` 持有一份，事件与审计各取一份
    快照，于是界面看到的、审计记下的、工具执行时用的是同一组值。
    """

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
        """进 ``run_started`` 事件的形状（三轴 + 沙箱 + 阶梯条数 + 审计状态）。"""
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
    """没有显式工作区根时用进程的工作区根。

    必须是**唯一**一处解析：``Ladder`` 的相对模式（``.env`` / ``.git/hooks``）与沙箱的
    ``--bind`` 都以"这次运行的工作区根"为基准，两处各算一次就会出现"策略按 A 判、
    沙箱按 B 挂"。惰性 import 是为了不与 ``tools`` 构成包级环（``tools/files.py`` 也
    import 本模块）。
    """
    from ..tools import workspace

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
    """三轴解析 + 阶梯装配 + 沙箱规格 + 审计落点。**唯一的装配点**。

    ``full`` 必须带 ``full_ack=True``（且 ``source`` 不能是 ``workspace_default``），
    否则抛 :class:`FullAccessError`——拒绝启动，而不是回落到别的模式。
    """
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
    """交互式确认。读不到输入一律拒绝。

    提示写 stderr，避免污染 stdout 上给用户看的最终答复。理由由引擎组装，分类前缀
    （"危险命令…" / "越界操作…" / "目标 … 在工作区之外"）与目标路径都在里面。
    """
    detail = json.dumps(arguments, ensure_ascii=False, default=str)
    # 并行 subagent 会同时来问，终端只有一个——串行化，否则提示会互相穿插。
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


# ``--yes`` 用的公开回答者：对每次询问都答"是"（硬拒绝与阶梯 deny 仍由引擎拦住）。
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
    """裁决一次调用，返回 **bool**（``gate`` 的薄封装，便于既有调用方与测试）。"""
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
    """``--yes`` 用：对本次运行的所有审批请求代答"是"，硬拒绝与阶梯 deny 仍然生效。

    它只改变"谁来回答"，不改变"哪些动作会打问号"（那由三轴决定），也**不关沙箱**。
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
    "CLASSIFIER_MESSAGE",
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
