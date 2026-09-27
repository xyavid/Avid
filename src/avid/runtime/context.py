"""上下文管线的编排：只在这里决定压缩步骤的顺序与条件。

``agent_loop`` 每轮调用一次 ``prepare()``，它不知道压缩有几步、谁先谁后、阈值是多少。

``summarize`` 作为参数传入，而不是本模块自己去 import 模型调用——这样 ①②③
在类型上就碰不到模型 API（不变量 I4），不需要运行时检查。

阈值的**来源**也在这里定：有窗口、且拿到过上一轮真实读数时，③④ 的字符阈值按窗口
现算（``effective_budget``）；缺任一项就逐字回落到 ``compact.CONTEXT_CHAR_LIMIT``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..ai.config import Config
from ..ai.transcript import Transcript
from ..policy import compaction as compact
from ..policy.compaction import CompactReport
from . import events
from .state import RunState

logger = logging.getLogger("avid.runtime.context")


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


@dataclass(frozen=True)
class Preparation:
    reports: list[CompactReport]

    @property
    def changed(self) -> bool:
        return bool(self.reports)


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


def prepare(
    transcript: Transcript,
    state: RunState,
    *,
    config: Config,
    summarize: Any,
    budget: ContextBudget | None = None,
) -> Preparation:
    """按代价从低到高跑一遍。

    ①② 每轮都跑；③ 超限时做免费瘦身；④ 只在"整理后仍超限"时才花一次模型调用，
    且整个运行最多一次（由 ``state.compacted`` 决定，只有这里写它）。

    阈值不在这里写死：``effective_budget`` 决定这次用的是派生值还是默认常量。
    """
    limits, note = effective_budget(budget or ContextBudget(), state)
    if note is not None:
        logger.debug("compact: %s", note)
    reports: list[CompactReport] = []
    # 压缩落盘跟着工作区走（阶段 18）：一个进程可以服务多个工作区。
    workdir = Path(state.workspace_root) if state.workspace_root else None

    def run(report: CompactReport | None) -> None:
        if report is None:
            return
        if note is not None:
            # 派生来的阈值要说清来历。detail 会进 context_compacted 事件，界面据此回答
            # "为什么现在压"；回落路径**不动** detail，默认行为因此逐字不变。
            report = replace(report, detail=f"{report.detail}（{note}）")
        reports.append(report)
        announce(report, state)

    # ①② 零 API，每轮都跑
    run(
        compact.tool_result_budget(
            transcript,
            budget=limits.tool_result_chars,
            keep_recent=limits.tool_result_keep_recent,
            workdir=workdir,
            tag=state.run_tag,
        )
    )
    run(
        compact.snip_compact(
            transcript,
            max_messages=limits.max_messages,
            keep_head=limits.keep_head,
            keep_tail=limits.keep_tail,
        )
    )

    # ③ 免费，先做
    if transcript.estimate_chars() > limits.context_chars:
        run(
            compact.micro_compact(
                transcript,
                limit=limits.context_chars,
                keep_recent=limits.micro_keep_recent,
                target_ratio=limits.micro_target_ratio,
                workdir=workdir,
                tag=state.run_tag,
            )
        )

    # ④ 整理后仍超限才付一次摘要调用；整个运行最多一次
    if transcript.estimate_chars() > limits.context_chars:
        if state.compacted:
            logger.info("compact: 自动压缩本运行已用过一次，跳过")
        else:
            report = compact.compact_history(
                transcript,
                config=config,
                chat=summarize,
                limit=limits.context_chars,
                workdir=workdir,
                tag=state.run_tag,
            )
            if report is not None:
                state.compacted = True
            run(report)

    return Preparation(reports=reports)


def reactive(
    transcript: Transcript,
    state: RunState,
    *,
    config: Config,
    summarize: Any,
    budget: ContextBudget | None = None,
) -> CompactReport | None:
    """兜底：模型已经报上下文超限，总结更早历史、保留最近若干条，供重试。

    调用方（循环）用 ``state.retried`` 保证整个运行只做一次；这里只负责
    "压一次 + 记一笔"。
    """
    limits = budget or ContextBudget()
    report = compact.reactive_compact(
        transcript,
        config=config,
        chat=summarize,
        workdir=Path(state.workspace_root) if state.workspace_root else None,
        keep_recent=limits.reactive_keep_recent,
        tag=state.run_tag,
    )
    announce(report, state)
    return report
