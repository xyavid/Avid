"""一次运行的可变状态。

替代原来分散的 3 个 ContextVar 与 4 个循环局部变量：轮次计数、一次性标志、
统计，以及运行期实例（TODO 列表、技能注册表、免审批开关）。

由 ``agent_loop`` 创建并**显式传给各环节**；不跨运行共享，也不跨线程继承——
子 agent 自建一份，所以 ``--yes`` 这类运行级开关必须显式传过去（这正是它该有的样子：
传不过去会立刻报错，而不是静默失效）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..policy.skills import SkillLoader
from ..policy.todo import TodoList, build_reminder

# TODO 提醒阈值：连续多少轮没更新就提醒一次。
# 它是**运行级配置**（循环的节奏）而不是策略层的规则，所以放这里——
# 循环取默认值时不必 import 策略层。
TODO_REMINDER_AFTER_ROUNDS = 3


@dataclass
class RunState:
    # 运行级开关
    auto_approve: bool = False

    # 轮次与终止
    round: int = 0
    rounds_since_todo: int = 0
    stop_blocks: int = 0

    # 一次性标志：谁负责"整个运行最多一次"
    compacted: bool = False
    retried: bool = False

    # 统计：进 Stop 事件，供 hook 与测试观察
    tool_calls: int = 0
    denials: int = 0
    compactions: int = 0

    # 运行期实例
    todo: TodoList = field(default_factory=TodoList)
    skills: SkillLoader = field(default_factory=SkillLoader)

    @classmethod
    def for_run(cls, *, auto_approve: bool = False) -> "RunState":
        """建一份运行状态，并**重新扫描一次技能目录**——磁盘变了，下次运行就生效。"""
        return cls(auto_approve=auto_approve, skills=SkillLoader().scan())

    def system_prompt(self, instructions: str | None = None) -> str:
        """固定指令 + 环境信息 + 技能目录。

        ``instructions`` 为 None 时用策略层的默认指令——于是循环不必知道那段默认文案。
        """
        if instructions is None:
            return self.skills.build_system_prompt()
        return self.skills.build_system_prompt(instructions)

    def snapshot(self) -> dict[str, int]:
        """Stop 事件要看到的运行统计。"""
        return {
            "rounds": self.round,
            "tool_calls": self.tool_calls,
            "denials": self.denials,
            "compactions": self.compactions,
        }

    def todo_reminder(self, threshold: int) -> str | None:
        """连续 threshold 轮没更新 TODO 时给出提醒文本，否则 None。

        "该不该提醒"与"提醒什么"都在这里，循环只负责把它追加进消息——
        于是循环不必 import 策略层（原本它要拿阈值常量与文案函数）。
        """
        if self.rounds_since_todo != threshold:
            return None
        return build_reminder(self.todo, self.rounds_since_todo)
