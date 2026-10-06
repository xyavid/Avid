"""Deterministic review for auto mode: prove a call safe, else hand it to a human.

分类器是风险分级器不是隔离边界（与 command_parse 同一原则）：它只回答「能不能
证明这次动作只落在工作区内」。证明不了不再自判拒绝，而是交人——拒绝发生在
无人可问的时候，由引擎负责；这条边界让无沙箱平台（Windows/macOS）的 auto
从「全拒」恢复为「分类放行 + 低频问人」。
"""

from __future__ import annotations

from dataclasses import dataclass

from .action import OUTSIDE_RISK, Action, exceeds_sandbox, is_mcp_tool
from .rules import VERDICT_ASK, VERDICT_DENY, Rule  # noqa: F401  (re-exported for type readers)
from .sandbox import SandboxSpec

# Verdict names for callers that log or branch on a review outcome.
ALLOW = "allow"
DENY = "deny"

# proven 档允许出现的能力全集：观察到集合之外的能力（删除、网络、解释器、
# 凭据、对外副作用…）就证明不了「只读」或「只写工作区」。bash 恒带
# process_spawn，所以它总在集合里；带状态能力的命令里出现 $ 一律在解析层
# 标 uncertain（写落点证明不了），因此进不了 proven 档——$_ 也不例外，
# 管道对象的属性同样可能是写目标。
READ_ONLY_CAPS = frozenset({"process_spawn", "filesystem_read"})
WORKSPACE_WRITE_CAPS = READ_ONLY_CAPS | {"filesystem_write"}


@dataclass(frozen=True)
class Review:
    """A review verdict; when ``allowed`` is false, ``reason`` must explain it to the human."""

    allowed: bool
    reason: str = ""


def _proven(action: Action, allowed_caps: frozenset[str]) -> bool:
    """A call is proven when every observed capability sits in the set and no fact flags it.

    「越界」只记录目标在工作区之外，读写都记；读区外是既有能力（沙箱 ``--ro-bind /``），
    真正的区外**写**已由 ``exceeds_sandbox`` 裁决，所以这里把它排除。
    """
    risks = [risk for risk in action.risks if risk != OUTSIDE_RISK]
    if risks or action.network or action.outside_writes:
        return False
    return action.capabilities <= allowed_caps


def classify(
    action: Action,
    *,
    rule: Rule | None = None,
    sandbox: SandboxSpec | None = None,
) -> Review:
    """Deterministic review of one action; ``rule`` is the ladder hit already found, if any.

    返回 False 的语义是「需要人」，不是「拒绝」。沙箱强制时普通命令在事实层就
    已放行，走到这里的只有规则/越界/危险/网络几类；沙箱降级时多出一档 proven
    判定——证明得了就裸跑，证明不了交人。
    """
    # ask 档规则（deny 档引擎更早已终局）与外部 MCP 的语义都是「人」的问题，分类器不代答。
    if rule is not None:
        return Review(False, f"该目标需要逐次批准（{rule.reason}）")
    if is_mcp_tool(action.tool):
        return Review(False, "外部 MCP 工具的语义不可静态判定")
    beyond = exceeds_sandbox(action)
    if beyond is not None:
        capability, target = beyond
        return Review(False, f"需要写沙箱保证之外的目标：{target}（{capability}）")
    if action.network:
        return Review(False, f"网络出口（{action.network_target or 'unknown'}）")
    # 「越界」是位置事实不是风险：读区外本来就是放行的能力，真正的区外写在上面已裁决。
    risks = [risk for risk in action.risks if risk != OUTSIDE_RISK]
    if risks:
        return Review(False, "、".join(risks))

    if sandbox is not None and sandbox.degraded:
        if _proven(action, READ_ONLY_CAPS) or _proven(action, WORKSPACE_WRITE_CAPS):
            return Review(True)
        return Review(
            False,
            f"沙箱不可用（{sandbox.reason}），且无法证明这次动作只落在工作区内",
        )
    return Review(True)


__all__ = ["ALLOW", "DENY", "READ_ONLY_CAPS", "Review", "WORKSPACE_WRITE_CAPS", "classify"]
