"""``auto`` 模式的审查者：确定性风险分类器。

原则⑥说 **auto-review 是护栏，不是硬边界**——它出错时兜底的是沙箱。这条定位决定了
实现口径：

* **不让模型当审查者**。原则②说 prompt 不是安全控制：把"这条命令危险吗"交给 LLM，
  得到的是一个可被同一段上下文影响的判断题。这里用确定性规则，输入是
  :class:`~avid.policy.action.Action`（归一化后的事实），输出可复现、可单测。
* **判不准就拒**。``auto`` 意味着**没有人在环**，所以分类器只有两个出口：放行或拒绝。
  "不确定"必须落到拒绝（失败关闭），并回一句可执行的下一步（换 manual、或让用户授权）。
* **它不解释沙箱**。分类器只回答"这个动作要不要被拦"，"就算放进去了能造成多大伤害"
  由沙箱负责——两者互不替代，这正是正交的含义。

规则顺序（第一条命中即定论）：

1. 命中 deny 阶梯（凭据 / 宿主策略）→ 拒；
2. ``ask`` 阶梯命中（例如 ``.env``）→ 拒（auto 下没有人可以回答这一问）；
3. 越过沙箱（写工作区与授予清单之外的目标）→ 拒；
4. 危险类别（提权、磁盘、服务、网络直接执行、容器、远程…）→ 拒；
5. 工作区在读或写、读整个文件系统、且没有任何风险信号 → 放行；
6. 沙箱要求了但不可用（降级）而这次动作会改变状态 → 拒。
"""

from __future__ import annotations

from dataclasses import dataclass

from .action import Action, exceeds_sandbox, is_mcp_tool
from .rules import VERDICT_ASK, VERDICT_DENY, Rule  # noqa: F401  (VERDICT_* 供类型读者)
from .sandbox import SandboxSpec

ALLOW = "allow"
DENY = "deny"


@dataclass(frozen=True)
class Review:
    """分类器的结论。``allowed`` 为假时 ``reason`` 必须能解释给模型听。"""

    allowed: bool
    reason: str = ""


def classify(
    action: Action,
    *,
    rule: Rule | None = None,
    sandbox: SandboxSpec | None = None,
) -> Review:
    """确定性审查。``rule`` 是阶梯已经命中的规则（如果命中过）。"""
    if rule is not None and rule.verdict == VERDICT_DENY:
        return Review(False, f"命中 {rule.tier} deny：{rule.reason}")
    if rule is not None and rule.verdict == VERDICT_ASK:
        return Review(False, f"该目标需要逐次批准（{rule.reason}），auto 下无人可答")

    if is_mcp_tool(action.tool):
        # 外部 MCP 工具的参数含义只有 server 自己知道，确定性分类器看不见它的语义；
        # "判不准就拒"在这里就是字面执行（manual 下它会问人，auto 没有人可问）。
        return Review(False, "外部 MCP 工具的语义不可静态判定，auto 下判不准即拒")

    beyond = exceeds_sandbox(action)
    if beyond is not None:
        capability, target = beyond
        return Review(False, f"需要写沙箱保证之外的目标：{target}（{capability}）")

    risks = [risk for risk in action.risks if risk != "越界"]
    if risks:
        return Review(False, f"命中危险类别：{'、'.join(risks)}")

    if sandbox is not None and sandbox.degraded and action.operations:
        return Review(False, f"沙箱不可用（{sandbox.reason}）且这次动作会改动状态")

    return Review(True)


__all__ = ["ALLOW", "DENY", "Review", "classify"]
