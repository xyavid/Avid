"""上下文装配器：模型每一轮看到什么，由这里单点决定。

以前「模型看到什么」散在多处——技能模板拼接（``policy/skills.py``）、hook 环境
注入（``runtime/hooks.py``）、TODO 提醒（``runtime/loop.py`` + ``policy/todo.py``）、
压缩编排（``runtime/context.py``）。新增一类上下文要同时改循环、hooks、skills
三处，占用台账也答不了「某类上下文占了多少」。现在所有进模型的内容先声明为
**块**，由本模块统一落位、记账与渲染：

* ``Block``：一段待进上下文的内容，带 kind（哪类上下文）与 section（落位）。
* ``SYSTEM`` 落位：渲染进系统提示词。**首轮定型、运行内不再变**——provider 的
  前缀缓存按请求前缀命中，系统提示词中途变化会把整段会话的缓存打穿。
* ``TAIL`` 落位：每轮重渲染的一条附加 user 消息，挂在请求**末尾**且**不落库**。
  计划与运行状态是派生事实（随时可从 ``RunState`` 重算），持久层只存用户原话
  与真实消息；放末尾是因为它每轮都变，放中间会打穿前缀缓存。
* 对话本体（用户问题、历史、工具调用与结果）就是 ``Transcript`` 本身：不经过
  sources，只参与记账，结构与唯一所有权仍归 ``ai/transcript.py``。

压缩是 history / tool_results 两个可变块的预算策略：五步管线留在
``policy/compaction.py``（签名不变，①②③ 在类型上碰不到模型 API），编排由本
模块承担（原 ``runtime/context.py`` 整体并入）——装配与预算同属一个所有者，
不再有两层各管一半。

新增一类上下文 = 写一个来源函数（返回 ``Block | None``）+ ``register_source``
一行，循环与 hooks 都不用动。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..ai.config import Config
from ..ai.transcript import Transcript, message_chars
from ..policy import compaction as compact
from ..policy.compaction import CompactReport
from . import events
from .state import RunState

logger = logging.getLogger("avid.runtime.context_manager")

# ---------------- 块模型 ----------------

#: section＝块落在哪里。system＝系统提示词（首轮定型）；tail＝每轮重渲染的末尾消息。
SYSTEM = "system"
TAIL = "tail"

#: 内置块种类。新种类不必改这里：``register_source`` 接受任意 kind 字符串。
INSTRUCTIONS = "instructions"  # 固定指令（调用方传入或默认文案）
ENVIRONMENT = "environment"  # 工作目录 + 可用工具
SKILL_CATALOG = "skill_catalog"  # 技能目录（全文要 load_skill 才进上下文）
INJECTED = "injected"  # UserPromptSubmit hook 注入的额外系统文本
PLAN = "plan"  # 当前 TODO 计划
RUN_STATE = "run_state"  # 轮次 / 工具调用 / 被拒 / 压缩计数

DEFAULT_INSTRUCTIONS = (
    "你是 Avid，一个能自主调用工具完成任务的 agent。"
    "需要外部信息或动作时调用工具；信息足够时直接给出答案。"
    "任务需要三步以上时，先用 todo_write 列出计划再逐步执行，"
    "每完成一步就重新提交整份列表并更新状态。"
)

# tail 消息的固定头：让模型（与读日志的人）一眼认出这不是用户说的话。
TAIL_HEADER = "[上下文] 以下是本次请求附带的运行时上下文，不是用户输入。"


@dataclass(frozen=True)
class Block:
    """一段待进上下文的内容。kind 标识类别，section 决定落位。"""

    kind: str
    content: str
    section: str


# ---------------- 预算（原 runtime/context.py 并入） ----------------


@dataclass(frozen=True)
class ContextBudget:
    """压缩阈值。默认值引用 compact 里的常量，保持单一来源。"""

    tool_result_chars: int = compact.TOOL_RESULT_CHAR_BUDGET
    tool_result_keep_recent: int = compact.TOOL_RESULT_KEEP_RECENT
    max_messages: int = compact.MAX_MESSAGES
    keep_head: int = compact.SNIP_KEEP_HEAD
    keep_tail: int = compact.SNIP_KEEP_TAIL
    context_chars: int = compact.CONTEXT_CHAR_LIMIT
    micro_keep_recent: int = compact.MICRO_COMPACT_KEEP_RECENT
    micro_target_ratio: float = compact.MICRO_COMPACT_TARGET_RATIO
    reactive_keep_recent: int = compact.REACTIVE_KEEP_RECENT
    # ③④ 的字符阈值是否跟随真实窗口（见 effective_budget）。**显式注入阈值做单变量
    # 对照时必须关掉它**：派生会盖掉调用方给的那个数，A/B 的差值就不再只来自那一个变量。
    from_window: bool = True
    # 派生时用掉窗口的多少比例（触发线）：派生出的阈值 ≈ 窗口 × 这个比例。
    window_ratio: float = compact.WINDOW_TRIGGER_RATIO


def effective_budget(
    limits: ContextBudget, state: RunState
) -> tuple[ContextBudget, str | None]:
    """这次运行实际用哪个阈值，以及它的来历（``None`` = 来历就是默认常量）。

    ③④ 的判据原先是一个写死的 400_000 字符：它与真实窗口无关，于是同一个内核在两种
    情形下差一个量级——中文（1 字符 ≈ 1 token）还没到 40 万字符就已经撞上 provider 的
    上下文超限，只剩 ⑤ 兜底；1M 窗口的模型则在用掉三分之一窗口时就被摘要。

    改成：有窗口、且拿到过一轮真实读数时，按``窗口 × window_ratio × 实测 chars/token
    −（系统提示 + 工具定义）``现算（``compact.derived_context_chars``）；缺任一项就
    **逐字**回落到 ``CONTEXT_CHAR_LIMIT``，默认路径因此不变。

    读数只有一处来源，且必须是**同一对**：``RunState.last_usage``（上一轮真实
    ``prompt_tokens``）与 ``RunState.prompt_parts``（发出那次请求**之前**记下的三块
    字符数）。循环先在发请求前记字符数、请求回来立刻记用量，所以这两个字段永远配套。

    为什么不去问 contextvars 或另存一份：阈值是"运行状态自身的量"，与用量台账同源，
    所以读的是 ``RunState`` 而不是新开一个通道。
    """
    if not limits.from_window:
        return limits, None

    parts = state.prompt_parts
    usage = state.last_usage
    derived = compact.derived_context_chars(
        window=state.context_window,
        prompt_tokens=None if usage is None else usage.prompt_tokens,
        chars=parts,
        ratio=limits.window_ratio,
    )
    if derived is None or parts is None:
        return limits, None

    limit, per_token = derived
    note = (
        f"阈值随窗口派生：{limit} 字符"
        f"（窗口 {state.context_window} × {limits.window_ratio:g} × "
        f"实测 {per_token:.2f} 字符/token − 系统与工具 {parts[0] + parts[1]} 字符）"
    )
    return replace(limits, context_chars=limit), note


def announce(report: CompactReport | None, state: RunState) -> None:
    """压缩发生时留下可观察的记录：一条日志 + 一次记账 + 一条事件。

    单点实现：循环里的兜底压缩也走它，避免日志格式、计数与事件三处维护。
    事件里带的是 ``CompactReport`` 的四个字段，前端据此显示"压缩发生了什么"；
    记账走 ``state.mark_compacted``——它同时置起"等下一轮真实读数"的标志，于是
    "压缩后还剩多少"由下一次模型调用回答，而不是在这里猜。
    """
    if report is None:
        return
    logger.info("compact: %s", report.describe())
    state.mark_compacted(report.step)
    state.emit(
        events.CONTEXT_COMPACTED,
        step=report.step,
        detail=report.detail,
        before=report.before,
        after=report.after,
    )


@dataclass(frozen=True)
class ComposedRequest:
    """一轮请求的最终形态：发什么 system、发什么 messages、各块占了多少字符。

    ``parts`` 按块种类记账（内部细分）；对外口径仍是三块——``system_chars`` 与
    ``messages_chars``，``tools`` 的字符数只有循环知道（工具定义不过这里）。
    """

    system: str
    messages: list[dict[str, Any]]
    parts: dict[str, int]
    reports: list[CompactReport]

    @property
    def changed(self) -> bool:
        """这次 compose 是否发生了压缩（循环据此打日志）。"""
        return bool(self.reports)

    @property
    def system_chars(self) -> int:
        return len(self.system)

    @property
    def messages_chars(self) -> int:
        return self.parts.get("messages", 0)


class ContextManager:
    """一次运行的上下文装配与预算。由 ``agent_loop`` 构造、逐轮调用。

    * ``compose()`` 每轮一次：先跑压缩（改 Transcript），再渲染请求。首轮把
      SYSTEM 块定格成系统提示词，此后运行内不变。
    * ``render()`` 兜底压缩后重渲染：不跑压缩、system 不变，只重算 tail 与记账。
    * ``reactive()`` 模型报超限后的兜底压缩（整个运行最多一次，由 state.retried 管）。
    """

    def __init__(
        self,
        *,
        transcript: Transcript,
        state: RunState,
        config: Config,
        instructions: str | None = None,
        tool_names: Iterable[str] = (),
        budget: ContextBudget | None = None,
        summarize: Any = None,
    ) -> None:
        self.transcript = transcript
        self.state = state
        self.config = config
        # ``instructions`` 是调用方的指令覆盖（bench / subagent 各有一份）；
        # None = 默认文案。循环不必再知道默认指令长什么样。
        self.instructions = (
            instructions if instructions is not None else DEFAULT_INSTRUCTIONS
        )
        self.tool_names = list(tool_names)
        self.budget = budget or ContextBudget()
        self.summarize = summarize
        self._sources: dict[str, Callable[[], Block | None]] = {}
        self._system: str | None = None
        self._system_parts: dict[str, int] = {}
        self._register_defaults()

    # ---- 来源注册 ----

    def register_source(self, kind: str, source: Callable[[], Block | None]) -> None:
        """注册或替换一类块的来源。SYSTEM 块只在首次 compose 生效（之后定格）。"""
        self._sources[kind] = source

    def _register_defaults(self) -> None:
        self.register_source(
            INSTRUCTIONS, lambda: Block(INSTRUCTIONS, self.instructions, SYSTEM)
        )
        self.register_source(
            ENVIRONMENT, lambda: Block(ENVIRONMENT, self._render_environment(), SYSTEM)
        )
        self.register_source(
            SKILL_CATALOG, lambda: Block(SKILL_CATALOG, self._render_skills(), SYSTEM)
        )
        self.register_source(PLAN, self._plan_block)
        self.register_source(RUN_STATE, self._run_state_block)

    # ---- 内置来源 ----

    def _render_environment(self) -> str:
        # 延迟导入：tools 包 import 了 policy.skills，顶层互相 import 会成环。
        from ..tools import workspace

        # 运行级工作区根优先（一个进程可以服务多个工作区），否则用进程默认根。
        root = self.state.workspace_root or workspace.WORKSPACE_ROOT
        names = "、".join(self.tool_names) if self.tool_names else "（无）"
        return f"## 环境\n工作目录：{root}\n可用工具：{names}\n\nAct, don't explain."

    def _render_skills(self) -> str:
        catalog = self.state.skills.catalog()
        return (
            "## 可用技能\n"
            f"{catalog or '（当前没有可用技能）'}\n\n"
            "Use load_skill to read the full instructions when a skill applies."
        )

    def _plan_block(self) -> Block | None:
        # TodoList.render 对空清单也给文案（「（列表为空）」），没有计划就整个不渲染。
        if not self.state.todo.items:
            return None
        return Block(PLAN, f"## 当前计划\n{self.state.todo.render()}", TAIL)

    def _run_state_block(self) -> Block:
        state = self.state
        return Block(
            RUN_STATE,
            "## 运行状态\n"
            f"第 {state.round} 轮；已执行 {state.tool_calls} 次工具调用；"
            f"被拒 {state.denials} 次（当前连击 {state.denial_streak}）；"
            f"压缩 {state.compactions} 次。",
            TAIL,
        )

    # ---- 收集与渲染 ----

    def _collect(self, section: str) -> list[Block]:
        blocks: list[Block] = []
        for source in self._sources.values():
            block = source()
            if block is not None and block.section == section and block.content:
                blocks.append(block)
        return blocks

    def compose(self, *, injected: list[str] | None = None) -> ComposedRequest:
        """压缩 + 渲染。``injected`` 是 UserPromptSubmit hook 的注入产物。

        它与 SYSTEM 块一起**只在首轮生效**（之后 system 已定格，再传也会被忽略）——
        注入的背景信息中途消失或变形，比一开始就没有更糟。
        """
        reports = self._compact()
        if self._system is None:
            self._freeze_system(list(injected or []))
        return self._assemble(reports)

    def system_prompt(self) -> str:
        """只渲染系统提示词：不压缩、不渲染 tail。

        bare 循环这类「无机制」基准臂用——它们的系统提示词与 avid 臂同源
        （同一批 SYSTEM 块），但不参与压缩与 tail。
        """
        if self._system is None:
            self._freeze_system([])
        return self._system

    def _freeze_system(self, injected: list[str]) -> None:
        blocks = self._collect(SYSTEM)
        texts = [block.content for block in blocks]
        texts += [str(text) for text in injected if str(text).strip()]
        self._system = "\n\n".join(texts)
        self._system_parts = {}
        for block in blocks:
            self._system_parts[block.kind] = (
                self._system_parts.get(block.kind, 0) + len(block.content)
            )
        for text in injected:
            self._system_parts[INJECTED] = (
                self._system_parts.get(INJECTED, 0) + len(str(text))
            )

    def render(self) -> ComposedRequest:
        """重渲染：兜底压缩改写 transcript 后重算 tail 与记账。system 不变、不压缩。"""
        if self._system is None:
            raise RuntimeError("render() 之前必须先 compose()")
        return self._assemble([])

    def _assemble(self, reports: list[CompactReport]) -> ComposedRequest:
        tail_blocks = self._collect(TAIL)
        messages = self.transcript.as_messages()
        tail_chars = 0
        if tail_blocks:
            content = TAIL_HEADER + "\n\n" + "\n\n".join(
                block.content for block in tail_blocks
            )
            message = {"role": "user", "content": content}
            messages.append(message)
            tail_chars = message_chars(message)

        parts = dict(self._system_parts)
        for block in tail_blocks:
            parts[block.kind] = parts.get(block.kind, 0) + len(block.content)
        parts["history"] = self.transcript.estimate_chars()
        parts["messages"] = parts["history"] + tail_chars
        return ComposedRequest(
            system=self._system or "",
            messages=messages,
            parts=parts,
            reports=reports,
        )

    # ---- 压缩编排（原 runtime/context.py 并入） ----

    def _compact(self) -> list[CompactReport]:
        """按代价从低到高跑一遍：①② 每轮都跑，③④ 超限时才做。

        ④ 整个运行最多一次（由 ``state.compacted`` 决定，只有这里写它）。
        阈值不在这里写死：``effective_budget`` 决定这次用的是派生值还是默认常量。
        """
        limits, note = effective_budget(self.budget, self.state)
        if note is not None:
            logger.debug("compact: %s", note)
        reports: list[CompactReport] = []
        # 压缩落盘跟着工作区走（阶段 18）：一个进程可以服务多个工作区。
        workdir = Path(self.state.workspace_root) if self.state.workspace_root else None

        def run(report: CompactReport | None) -> None:
            if report is None:
                return
            if note is not None:
                # 派生来的阈值要说清来历。detail 会进 context_compacted 事件，界面据此
                # 回答"为什么现在压"；回落路径不动 detail，默认行为因此逐字不变。
                report = replace(report, detail=f"{report.detail}（{note}）")
            reports.append(report)
            announce(report, self.state)

        # ①② 零 API，每轮都跑
        run(
            compact.tool_result_budget(
                self.transcript,
                budget=limits.tool_result_chars,
                keep_recent=limits.tool_result_keep_recent,
                workdir=workdir,
                tag=self.state.run_tag,
            )
        )
        run(
            compact.snip_compact(
                self.transcript,
                max_messages=limits.max_messages,
                keep_head=limits.keep_head,
                keep_tail=limits.keep_tail,
            )
        )

        # ③ 免费，先做
        if self.transcript.estimate_chars() > limits.context_chars:
            run(
                compact.micro_compact(
                    self.transcript,
                    limit=limits.context_chars,
                    keep_recent=limits.micro_keep_recent,
                    target_ratio=limits.micro_target_ratio,
                    workdir=workdir,
                    tag=self.state.run_tag,
                )
            )

        # ④ 整理后仍超限才付一次摘要调用；整个运行最多一次
        if self.transcript.estimate_chars() > limits.context_chars:
            if self.state.compacted:
                logger.info("compact: 自动压缩本运行已用过一次，跳过")
            else:
                report = compact.compact_history(
                    self.transcript,
                    config=self.config,
                    chat=self.summarize,
                    limit=limits.context_chars,
                    workdir=workdir,
                    tag=self.state.run_tag,
                )
                if report is not None:
                    self.state.compacted = True
                run(report)

        return reports

    def reactive(self) -> CompactReport | None:
        """兜底：模型已经报上下文超限，总结更早历史、保留最近若干条，供重试。

        调用方（循环）用 ``state.retried`` 保证整个运行只做一次；这里只负责
        "压一次 + 记一笔"。
        """
        report = compact.reactive_compact(
            self.transcript,
            config=self.config,
            chat=self.summarize,
            workdir=Path(self.state.workspace_root)
            if self.state.workspace_root
            else None,
            keep_recent=self.budget.reactive_keep_recent,
            tag=self.state.run_tag,
        )
        announce(report, self.state)
        return report
