"""Policy Engine：把 :class:`~avid.policy.action.Action` 变成一次裁决。

决策表的形状（**顺序即优先级**，``deny`` 全局高于 ``ask``，``ask`` 高于 ``allow``）：

===================  =============================================  ========================
第一步               判什么                                          出口
===================  =============================================  ========================
1 硬拒绝             ``rm -rf /``、``mkfs``、写块设备、关机            deny（任何模式、任何人）
2 阶梯               ADMIN / SYSTEM / PROJECT deny                    deny（**包括 full**）
2 阶梯（ask 档）     敏感但合法的目标（``.env``）                     REVIEW
3 越界               目标在工作区之外                                 REVIEW
4 危险              提权 / 磁盘 / 服务 / 网络直接执行 / 容器 / 远程    REVIEW
5 降级               要沙箱而沙箱不可用，且动作会改动状态              REVIEW
6 成本              ``subagent`` 这类"不危险但花钱"的动作             REVIEW
7 其余              区内只读、区内写入（沙箱保证）                    allow
===================  =============================================  ========================

REVIEW 由三轴的 ``approval`` 回答：

* ``user``：问人（同意一次即记进能力账本，同一次运行不再问）；
* ``classifier``：确定性审查（判不准即拒），**不问人**；
* ``none``（full）：直接放行，但审计里留下 ``answered_by=none``。

**full 不等于无所不能**：第 1、2 步在任何模式下都成立。ADMIN DENY 是"人也不能覆盖"的
一层，这正是"deny 高于 allow"的实际含义。
"""

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
)
from .classifier import classify
from .modes import (
    APPROVAL_CLASSIFIER,
    APPROVAL_NONE,
    APPROVAL_USER,
    DEFAULT_MODE,
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

# 阶梯自己的 "deny" 与引擎的 `VERDICT_DENY` 是同一个字符串（规格里 deny 只有一种含义），
# 但两个模块各自是权威，所以显式别名、不共用名字：避免"用的是导入的还是新定义的"歧义。
from .rules import VERDICT_DENY as RULE_DENY
from .sandbox import UNMANAGED, SandboxSpec

VERDICT_ALLOW = "allow"
VERDICT_ASK = "ask"
VERDICT_DENY = "deny"

# 对外的结构化终局。verdict/kind 继续保留，供旧事件与审计消费者兼容。
SAFE_AUTO = "SAFE_AUTO"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
SANDBOX_DENIED = "SANDBOX_DENIED"
POLICY_DENIED = "POLICY_DENIED"

KIND_HARD = "hard"
KIND_CREDENTIAL = "credential"
KIND_RULE = "rule"
KIND_OUTSIDE = "outside"
KIND_DANGER = "danger"
KIND_DEGRADED = "degraded"
KIND_NETWORK = "network"
KIND_COST = "cost"

#: 回传给模型的文案。三类拒绝给不同的下一步指引（"永远不许"与"这次不行"对模型意味着
#: 完全不同的事，混为一谈会让它反复重试）。
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
    "Permission denied. 原因：目标在工作区之外且未获批准（{reason}）。"
    "不要重复尝试同一路径；请在工作区内完成，或说明为什么需要它。"
)
DEGRADED_MESSAGE = (
    "Permission denied. 原因：沙箱不可用（{reason}），当前模式不再信任自动化放行。"
    "请让用户在 manual 模式下逐条批准，或先装好 bubblewrap。"
)
NETWORK_MESSAGE = (
    "SANDBOX_NETWORK_DENIED: network_connect 被网络沙箱禁止（{reason}）。"
    "请勿重试同一网络请求；由用户显式调整网络能力或使用已授权的工具。"
)
CLASSIFIER_MESSAGE = (
    "Permission denied. 原因：自动审查判定风险过高（{reason}）。"
    "auto 模式下无人可以批准它；请改用非破坏性做法，或让用户切到 manual 后自己批准。"
)
USER_MESSAGE = (
    "Permission denied. 原因：本次未获用户批准。"
    "不要重复提交同一条调用；请说明你需要它做什么，或改用其它工具。"
)

MESSAGE_FOR: dict[str, str] = {
    KIND_HARD: HARD_MESSAGE,
    KIND_CREDENTIAL: CREDENTIAL_MESSAGE,
    KIND_RULE: RULE_MESSAGE,
    KIND_DANGER: DANGER_MESSAGE,
    KIND_OUTSIDE: OUTSIDE_MESSAGE,
    KIND_DEGRADED: DEGRADED_MESSAGE,
    KIND_NETWORK: NETWORK_MESSAGE,
    KIND_COST: USER_MESSAGE,
}

AskUser = Callable[[str, dict[str, Any], str], bool]


class Ledger(Protocol):
    """能力账本的最小接口（实现见 :class:`avid.policy.permission.ApprovalLedger`）。

    引擎不 import 门面模块（门面 import 引擎），所以这里只声明形状。
    """

    def knows(self, key: tuple[str, ...]) -> bool: ...

    def has_capability(self, kind: str, value: str) -> bool: ...

    def remember(self, key: tuple[str, ...]) -> None: ...


@dataclass(frozen=True)
class Decision:
    """一次裁决的完整结果：给机器看的出口、给人看的理由、给模型看的文案。"""

    verdict: str
    kind: str = ""
    tier: str = ""
    reason: str = ""
    message: str = ""
    key: tuple[str, ...] | None = None
    answered_by: str = ""
    #: 批准时应当授予的能力（路径 → ro/rw）。由沙箱在组装 argv 时使用。
    grants: tuple[tuple[str, str], ...] = ()
    code: str = ""
    operation: str = ""
    target: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == VERDICT_ALLOW

    @property
    def type(self) -> str:
        """规范化的四类命令终局，供工具执行器与 API 使用。

        ``verdict``/``kind`` 是历史兼容字段；这个属性把 Policy、Approval、Sandbox
        的结果压缩成调用方可稳定分派的类型。``degraded`` 明确属于沙箱边界失败，不能
        被误报成普通策略拒绝。
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
        """``type`` 的显式别名，避免调用方与 Python 内建名混淆。"""
        return self.type

    def __bool__(self) -> bool:  # 既有调用方按 bool(decision) 判断
        return self.allowed


def access_for(action: Action) -> str:
    """这次能力授予按什么口径挂载：写类给 rw，其余给 ro（原则⑦：授予尽量窄）。"""
    return "rw" if action.operations[:1] == (OPERATION_WRITE,) else "ro"


def ladder_hit(action: Action, ladder: Ladder | None) -> Rule | None:
    """在动作的**所有**目标上查阶梯，返回优先级最高的那条命中。

    bash 的目标可能不止一个（``cat /etc/hosts ~/.ssh/id_rsa``），逐个查才不会因为
    "第一个目标很干净"而漏掉后面那个。
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
    hits.sort(key=lambda rule: (rule.verdict != RULE_DENY, TIER_ORDER[rule.tier]))
    return hits[0]


def review_key(action: Action) -> tuple[str, ...]:
    """REVIEW 的记账键：bash 按归一化命令原文，其它按第一个目标。

    "同意一次即生效"的粒度就是它——同一条命令重跑不再问，换个命令照问。
    """
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
    """这一步要不要 REVIEW、给人看的理由、以及给文案用的细节。

    返回 ``(kind, reason, detail)``；``None`` 表示没有任何 REVIEW 理由。``detail`` 与
    ``reason`` 分开，是因为拒绝文案里再嵌一次完整理由会读出"命中安全策略（安全策略
    要求逐次批准（…））"这种套娃——文案是给模型看的，重复只会稀释信息。
    """
    if rule is not None:
        kind = KIND_CREDENTIAL if rule.tier == TIER_ADMIN else KIND_RULE
        prefix = "受保护的宿主资源" if rule.tier == TIER_ADMIN else "安全策略要求逐次批准"
        return kind, f"{prefix}（{rule.reason}）", rule.reason

    if action.outside:
        target = action.outside[0]
        return KIND_OUTSIDE, f"目标 {target} 在工作区之外", target

    risks = [risk for risk in action.risks if risk != "越界"]
    if risks:
        joined = "、".join(risks)
        return KIND_DANGER, joined, joined

    if sandbox is not None and sandbox.degraded and action.tool in APPROVAL_RULES:
        # 只把**受管工具**打回 REVIEW：区内读取本来就由路径校验保证（"区内读取从不
        # 审批"是既有规格），沙箱不可用时再问一遍只是噪音。
        detail = sandbox.reason or "后端不可用"
        return KIND_DEGRADED, f"沙箱不可用（{detail}）", detail

    if action.tool in COST_RULES:
        return KIND_COST, COST_RULES[action.tool], COST_RULES[action.tool]

    return None


def _grant_key(action: Action) -> tuple[tuple[str, str], ...]:
    access = access_for(action)
    return tuple((target, access) for target in action.outside)


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
    """唯一的裁决入口。参数都是**事实**，函数本身不做 IO（除了 ``ask`` 会阻塞等答复）。"""
    spec = mode_spec(mode) if isinstance(mode, str) else mode
    approval = spec.approval
    # 没有规格 = 要求沙箱但没有后端（见 sandbox.UNMANAGED）：失败关闭，而不是无边界。
    sandbox = sandbox if sandbox is not None else UNMANAGED

    # 1 硬拒绝：任何模式、任何回答都不放行。
    if action.damage:
        return Decision(
            VERDICT_DENY,
            KIND_HARD,
            TIER_ADMIN,
            action.damage,
            HARD_MESSAGE.format(reason=action.damage),
        )

    # 2 阶梯：deny 命中即终局（full 也不例外）；ask 命中进入 REVIEW。
    rule = ladder_hit(action, ladder)
    if rule is not None and rule.verdict == RULE_DENY:
        kind = KIND_CREDENTIAL if rule.tier == TIER_ADMIN else KIND_RULE
        reason = f"{rule.reason}（{rule.tier}）"
        return Decision(VERDICT_DENY, kind, rule.tier, reason, _message(kind, rule.reason))

    # 只读系统 + 单文件能力挂载无法创建不存在的区外路径。
    if sandbox.enforced and action.tool == "bash" and action.operations[:1] == (OPERATION_WRITE,):
        missing = next((path for path in action.outside
                        if not Path(path).exists()
                        and not Path(path).is_relative_to("/tmp")
                        and not Path(path).is_relative_to("/dev/tcp")
                        and not Path(path).is_relative_to("/dev/udp")), None)
        if missing is not None:
            return Decision(
                VERDICT_DENY, KIND_OUTSIDE, "", f"沙箱无法挂载不存在的区外目标 {missing}",
                f"SANDBOX_FILESYSTEM_DENIED: filesystem_write 超出可挂载的路径（{missing}）。",
                code="SANDBOX_FILESYSTEM_DENIED", operation="filesystem_write", target=missing,
            )

    # 沙箱决定资源上限，与命令是否被人批准正交。限制网络时即使审批通过也
    # 没有出网能力：在执行前告知结构化错误，不能等 curl 的 DNS 错误冒充结论。
    if action.network and sandbox.network == "restricted":
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

    # 3 approval=none（full）：不设问，但记下"没人拦过"。
    if approval == APPROVAL_NONE:
        return Decision(VERDICT_ALLOW, kind, "", reason, answered_by="none", key=key)

    # 4 账本复用：同一能力同意过一次就够。
    path_access = access_for(action)
    paths_granted = bool(action.outside) and all(
        ledger.knows(("path", target, path_access))
        or (path_access == "ro" and ledger.knows(("path", target, "rw")))
        for target in action.outside
    ) if ledger is not None else False
    if ledger is not None and (ledger.knows(key) or paths_granted):
        return Decision(VERDICT_ALLOW, kind, "", reason, key=key, answered_by="ledger")

    # 5 approval=classifier：确定性审查，判不准即拒。
    if approval == APPROVAL_CLASSIFIER:
        review = classify(action, rule=rule, sandbox=sandbox)
        if review.allowed:
            return Decision(VERDICT_ALLOW, kind, "classifier", reason, answered_by="classifier")
        return Decision(
            VERDICT_DENY,
            kind,
            "classifier",
            reason,
            CLASSIFIER_MESSAGE.format(reason=review.reason),
            key=key,
            answered_by="classifier",
        )

    # 6 approval=user：问人。问不到（没有回答者 / EOF / 超时）就是拒绝。
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
    """``brokerize`` + ``decide`` 的组合入口（既有调用方的形状）。

    ``danger`` / ``outside`` 是**调用方算好的事实**覆盖（测试与直调用路径）：给了就用
    它，没给就用 broker 从参数里算出来的。

    ``ladder`` 缺省时按 ``root`` **现装一份阶梯**（读宿主策略文件，代价很小）。这条缺省
    是刻意的失败方向：漏传阶梯等于"没有 deny 规则"，那会让宿主级禁令静默消失；
    现装一份最坏是"读了一次配置"，永远不会更宽。
    """
    built = action if action is not None else brokerize(name, arguments, root=root)
    if ladder is None:
        ladder = Ladder.load(root=root)
    if danger is not None or outside is not None:
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
    "CLASSIFIER_MESSAGE",
    "CREDENTIAL_MESSAGE",
    "DANGER_MESSAGE",
    "DEGRADED_MESSAGE",
    "Decision",
    "HARD_MESSAGE",
    "NETWORK_MESSAGE",
    "NEEDS_APPROVAL",
    "POLICY_DENIED",
    "SAFE_AUTO",
    "SANDBOX_DENIED",
    "KIND_COST",
    "KIND_CREDENTIAL",
    "KIND_DANGER",
    "KIND_DEGRADED",
    "KIND_HARD",
    "KIND_NETWORK",
    "KIND_OUTSIDE",
    "KIND_RULE",
    "Ledger",
    "MESSAGE_FOR",
    "OUTSIDE_MESSAGE",
    "RULE_MESSAGE",
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
