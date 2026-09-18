"""上下文管线的编排：只在这里决定压缩步骤的顺序与条件。

``agent_loop`` 每轮调用一次 ``prepare()``，它不知道压缩有几步、谁先谁后、阈值是多少。

``summarize`` 作为参数传入，而不是本模块自己去 import 模型调用——这样 ①②③
在类型上就碰不到模型 API（不变量 I4），不需要运行时检查。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Preparation:
    reports: list[CompactReport]

    @property
    def changed(self) -> bool:
        return bool(self.reports)


def announce(report: CompactReport | None, state: RunState) -> None:
    """压缩发生时留下可观察的记录：一条日志 + 一个计数 + 一条事件。

    单点实现：循环里的兜底压缩也走它，避免日志格式、计数与事件三处维护。
    事件里带的是 ``CompactReport`` 的四个字段，前端据此显示"压缩发生了什么"。
    """
    if report is None:
        return
    logger.info("compact: %s", report.describe())
    state.compactions += 1
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
    """
    limits = budget or ContextBudget()
    reports: list[CompactReport] = []
    # 压缩落盘跟着工作区走（阶段 18）：一个进程可以服务多个工作区。
    workdir = Path(state.workspace_root) if state.workspace_root else None

    def run(report: CompactReport | None) -> None:
        if report is None:
            return
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
