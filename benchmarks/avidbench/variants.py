"""变体：三个臂的唯一事实来源。

消融表（v0.1 只有三臂，理由见 `benchmarks/README.md`）：

| 变体   | 循环                | 工具集                        | 压缩/提醒/nudge/hook |
|--------|---------------------|-------------------------------|----------------------|
| bare   | `bare.py` 朴素循环  | read_file / glob / bash       | 关                   |
| core   | `agent_loop`        | 与 bare **完全相同**          | 开                   |
| full   | `agent_loop`        | 全部（+ todo/task/subagent/skill） | 开              |

于是 `core − bare` 度量"循环与上下文机制"的增益，`full − core` 度量"子 agent /
子 agent / 技能"的增益。**三者共用同一份 `SYSTEM_PROMPT`**——prompt 不同源的话，
量到的是 prompt 差异，不是机制的差异。

每次运行都会把自己的规格写进 `result.json` 的 `variant_spec`；以后加了臂，
历史数字仍然可解释。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from avid.ai.client import Config
from avid.runtime.hooks import HookRegistry
from avid.runtime.loop import agent_loop
from avid.runtime.state import RunState
from avid.tools import TOOL_IMPLS, TOOLS

from .bare import bare_loop

if TYPE_CHECKING:
    from avid.runtime.context import ContextBudget

#: 三个臂共用的系统提示词。不含任何工具名——工具清单由循环按变体注入，
#: 否则 bare 会被要求去调用它没有的工具。
SYSTEM_PROMPT = (
    "你是 Avid，一个能自主调用工具完成任务的 agent。"
    "需要外部信息或动作时调用工具；信息足够时直接给出答案。"
    "严格遵守题目要求的回答格式，格式里的标签一个字都不要改。"
)

#: bare 与 core 的工具集：只读任务够用的三个。
CORE_TOOLS = ("read_file", "glob", "bash")


class VariantError(Exception):
    """变体名不存在。"""


@dataclass(frozen=True)
class Variant:
    name: str
    loop: str  # "bare" | "avid"
    tools: tuple[str, ...] | None  # None = 全部注册工具
    compaction: bool
    todo_reminder: bool
    stop_nudge: bool
    hooks: bool
    description: str

    @property
    def all_tools(self) -> tuple[str, ...]:
        if self.tools is None:
            return tuple(str(item["function"]["name"]) for item in TOOLS)
        return self.tools

    def spec_dict(self) -> dict[str, Any]:
        """写进 `result.json` 的规格快照。"""
        return {
            "name": self.name,
            "loop": self.loop,
            "tools": list(self.all_tools),
            "mechanisms": {
                "compaction": self.compaction,
                "todo_reminder": self.todo_reminder,
                "stop_nudge": self.stop_nudge,
                "hooks": self.hooks,
            },
            "description": self.description,
        }


VARIANTS: dict[str, Variant] = {
    "bare": Variant(
        name="bare",
        loop="bare",
        tools=CORE_TOOLS,
        compaction=False,
        todo_reminder=False,
        stop_nudge=False,
        hooks=False,
        description="自写朴素循环 + 核心只读工具；Avid 机制全关（基准线）",
    ),
    "core": Variant(
        name="core",
        loop="avid",
        tools=CORE_TOOLS,
        compaction=True,
        todo_reminder=True,
        stop_nudge=True,
        hooks=True,
        description="真 agent_loop + 压缩/提醒/hook；工具集与 bare 相同",
    ),
    "full": Variant(
        name="full",
        loop="avid",
        tools=None,
        compaction=True,
        todo_reminder=True,
        stop_nudge=True,
        hooks=True,
        description="全部工具与机制（含 todo / subagent / skill）",
    ),
}


def spec(name: str) -> Variant:
    if name not in VARIANTS:
        raise VariantError(f"未知变体 {name!r}；可用：{'、'.join(VARIANTS)}")
    return VARIANTS[name]


def schemas(variant: Variant) -> list[dict[str, Any]]:
    """发给模型的工具定义（按注册表顺序裁剪）。"""
    wanted = set(variant.all_tools)
    return [item for item in TOOLS if str(item["function"]["name"]) in wanted]


def registry(variant: Variant) -> dict[str, Any]:
    wanted = set(variant.all_tools)
    return {name: impl for name, impl in TOOL_IMPLS.items() if name in wanted}


def hooks_for(variant: Variant) -> HookRegistry | None:
    """`None` = 用进程级 `DEFAULT_HOOKS`（core / full）；空注册表 = 一个 hook 都不挂（bare）。

    重复调用提醒、截断落盘都属于被消融的机制，所以 bare 必须拿到空表。
    它要在建 `RunState` 时生效——`agent_loop` 的 `hooks` 参数在传了 `state`
    时不起作用（那份 state 是唯一权威）。
    """
    return None if variant.hooks else HookRegistry()


def run_agent(
    variant: Variant,
    *,
    messages: list[dict[str, Any]],
    config: Config,
    chat: Callable[..., Any],
    state: RunState,
    max_parallel: int = 1,
    on_message: Callable[[dict[str, Any]], Any] | None = None,
    budget: ContextBudget | None = None,
) -> str:
    """按变体调用对应的循环。返回最终 assistant 文本。

    ``budget`` 只对 `avid` 循环有效：`bare` 没有任何压缩机制，给它注入阈值等于无声
    无效——所以调用方只在被消融的臂上用（`runner` 就是这么做的，且把它写进 overrides）。

    没有轮数上限：两条循环都不设，预算由 ``case.limits.timeout_seconds`` 的墙钟
    watchdog 兜。

    ``max_parallel`` 缺省 1：评测基线是串行量的，消融的变量是"机制有没有"，不该
    顺带换掉工具派发方式。要做「串行 vs 并行」对照就显式传（见
    `benchmarks/parallel_tools/`）。
    """
    system = state.system_prompt(SYSTEM_PROMPT)
    if variant.loop == "bare":
        return bare_loop(
            messages,
            system=system,
            tools=schemas(variant),
            registry=registry(variant),
            config=config,
            chat=chat,
            state=state,
            max_parallel=max_parallel,
            on_message=on_message,
        )
    return agent_loop(
        messages,
        system=system,
        tools=schemas(variant),
        registry=registry(variant),
        config=config,
        chat=chat,
        state=state,
        max_parallel_tools=max_parallel,
        on_message=on_message,
        budget=budget,
    )
