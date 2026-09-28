"""Agent 循环：只表达调度顺序。

它不认识阈值、文案与协议细节——

* 消息结构与不变量：``ai/transcript.py``
* 运行状态与一次性标志：``runtime/state.py``
* 上下文装配与压缩编排：``runtime/context_manager.py``
* 工具调用协议：``runtime/execution.py``
* 扩展点：``runtime/hooks.py``

它对 ``policy/`` **零依赖**——阈值、文案、规则与注册表都经 ``state`` 与事件间接取得。

它只回答：什么时候调模型、什么时候跑工具、什么时候停。

协议映射（Anthropic 语义 → OpenAI 兼容）：
  content 里的 tool_use 块        → message.tool_calls[]
  tool_result 的一条 user 消息    → 每次调用一条 {"role": "tool", ...}
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..ai.client import (
    DEFAULT_MAX_TOKENS,
    PromptTooLongError,
    Turn,
    chat_completion,
    fetch_context_length,
)
from ..ai.config import Config, load_config
from ..ai.transcript import Transcript
from ..tools import TOOL_IMPLS, TOOLS, ToolImpl
from . import events
from .context_manager import ComposedRequest, ContextBudget, ContextManager
from .events import RunObserver
from .execution import execute_batch
from .hooks import BLOCK, HookRegistry
from .state import MAX_CONSECUTIVE_DENIALS, RunState

if TYPE_CHECKING:  # 只有类型标注用它：注解是惰性的，运行时不必跨层 import 策略层
    from ..policy.permission import ApprovalLedger, AskUser, RunSecurity

logger = logging.getLogger("avid.runtime.loop")

# Stop 被拦截后最多再补几轮。防止写坏的回调把循环拖成死循环。
MAX_STOP_BLOCKS = 1

#: 一轮**没有可见正文**（被输出上限截断，或只产出了思考）时的补问文案。
#: 这一轮不算「答完了」：它按一次 Stop 拦截处理，所以共用同一份补问预算。
BLANK_ANSWER_NUDGE = (
    "上一轮没有可见正文（{reason}）。请直接给出可见答复：总结已完成的事与当前结论；"
    "要继续动手就发起工具调用。"
)
#: 补问后仍然没有正文时的收尾文案。**必须可见**：空答复加「运行成功」是最坏的一种
#: 静默失败——前端把空正文的 assistant 条目整条隐藏，用户看到的是没有任何解释的结束。
BLANK_ANSWER_NOTICE = (
    "（本次运行没有产生可见答复：{reason}。请看上一条工具结果，或重试这一轮。）"
)


class RunCancelled(RuntimeError):
    """运行被显式取消（终止原因，不是错误）。

    它只在**步骤边界**抛出：在飞的模型调用或工具调用结束后（§7.4）。已产生
    的消息在产生时就已落库，所以取消不需要补偿写，也不产生伪造的工具结果
    （不变量 I9）。
    """


def _submit_input(
    transcript: Transcript, state: RunState, tool_names: list[str]
) -> tuple[int, list[str]] | None:
    """UserPromptSubmit：可注入上下文，也可拦截整个输入。

    返回**触发消息下标**与本次注入的上下文条目；None 表示这次不跑（没有用户消息，
    或被拦截）。

    ``tool_names`` 是本次运行真正发给模型的工具名：注入的环境信息必须与它一致，
    否则 subagent 的系统提示会宣称自己能用 ``subagent``（它只带 ``SUB_TOOLS``）。

    注入**不改写用户消息**——它由调用方并进系统提示词。以前把注入拼在 user content
    前面，于是「用户说的话」里混进了内核写的环境信息：界面无从分辨（它确实就是一条
    普通的 user 消息），落库也跟着存了注入后的版本。
    """
    index = transcript.last_user_index()
    if index is None:
        return None

    submit: dict[str, Any] = {
        "prompt": transcript.text_at(index),
        "messages": transcript.as_messages(),
        "injected": [],
        # 运行级工作区根：注入给模型的环境信息要与实际解析一致。
        "workspace_root": state.workspace_root,
        "permission_mode": state.permission_mode,
        "tool_names": list(tool_names),
    }
    if state.hooks.trigger("UserPromptSubmit", submit) == BLOCK:
        logger.warning("UserPromptSubmit 被拦截，未调用模型")
        return None

    return index, [str(item) for item in (submit.get("injected") or [])]


def _blank_answer(turn: Turn) -> bool:
    """这一轮算不算「没有可见正文」。只在 `not turn.tool_calls` 的分支里用。

    判据是「正文为空」而不是某个特定的 finish_reason：被上限截断（length）与模型自己
    交白卷（stop）对用户是同一件事——都没有答复。
    """
    return not turn.text.strip()


def _blank_reason(turn: Turn) -> str:
    """这一轮为什么没有可见正文。给用户看的，所以用具体数字，不用"未知原因"。"""
    thinking = turn.reasoning.strip()
    if thinking:
        base = f"最近一轮只产出了思考（{len(thinking)} 字符思维链）"
    elif turn.finish_reason == "length":
        base = "最近一轮在输出上限处被截断"
    else:
        base = "最近一轮输出为空"
    tokens = turn.usage.reasoning_tokens
    return f"{base}，推理 token {tokens}" if tokens else base


def agent_loop(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    config: Config | None = None,
    chat: Callable[..., Turn] = chat_completion,
    # 摘要类调用（压缩历史、兜底压缩）单独一个入口，默认与 chat 同一个。
    # 为什么要能分开：主轮次的输出是**对话内容**，摘要不是。svc 把主轮次接成流式、
    # 摘要接成非流式，delta 流里就不会混进摘要文本（否则它会与真正的回复粘成一条气泡）。
    summarize: Callable[..., Turn] | None = None,
    # 压缩阈值。None = `ContextBudget()` 的默认值（行为与以前逐字一致）。
    # 为什么要能注入：压缩的收益与代价（省多少 token / 会不会丢早期事实）只能靠
    # **同一个任务集跑两组不同阈值**量出来，而"改常量再跑一次"会把代码差异混进差值里。
    # 这是评测唯一要的内核开口，默认路径一行未变。
    budget: ContextBudget | None = None,
    auto_approve: bool = False,
    # 权限模式（strict / workspace / system）与"同意一次"账本。None 交给 RunState
    # 取默认（循环不认识策略层的默认值）。账本由调用方传入时与子 agent 共用，
    # 于是同一项操作的同意覆盖整个运行。
    permission_mode: str | None = None,
    ledger: "ApprovalLedger | None" = None,
    security: "RunSecurity | None" = None,
    workspace_root: str | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    # 连续被拒到这个数（期间没有一次通过）就停止整个运行。见 state 里的常量说明。
    max_consecutive_denials: int = MAX_CONSECUTIVE_DENIALS,
    # 一步内并行工具调用的上限。None = 用 `config.max_parallel_tool_calls`
    # （环境变量 `AVID_MAX_PARALLEL_TOOL_CALLS`，缺省 10）；1 = 完全串行。
    # 只有**并发安全**的工具会被并进同一段，写类/bash/任务类/子 agent 是串行屏障
    # （分类见 `tools/safety.py`，调度见 `execution.execute_batch`）。
    max_parallel_tools: int | None = None,
    on_message: Callable[[dict[str, Any]], Any] | None = None,
    ask: AskUser | None = None,
    on_event: RunObserver | None = None,
    state: RunState | None = None,
    hooks: HookRegistry | None = None,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    ``messages`` 原地更新：每轮的 assistant 消息与工具结果都会写回同一个 list。
    ``system`` 是指令覆盖（None = 默认文案）；环境、技能目录与每轮的 tail 块
    由 ContextManager 统一装配，循环不再自己拼系统提示词。

    ``on_message`` 是循环**唯一的消息通道**：本次运行产生或改写的每条消息按
    发生顺序回调一次——先是触发用户消息（UserPromptSubmit 注入**之后**的版本），
    然后是每轮追加的 assistant、工具结果与 Stop nudge。计划与运行状态走 tail 块
    （不落库），不经过这条通道。循环不 import 会话层，落库与否由回调决定（不变量 I7）。

    ``on_event`` 是**步骤级事实**的通道（轮次、Stop nudge、取消），
    不是第二个消息通道：``on_message`` 仍然是消息的唯一出口。两者都不改调度。

    ``hooks`` 注入这次运行的 hook 注册表（None = 进程级默认）：以前注册表是模块级
    字典，一次注册会漏到同进程所有运行。

    ``budget`` 注入这次运行的压缩阈值（None = ``ContextBudget()`` 的默认值）：压缩的
    收益与代价（省多少 token / 会不会丢早期事实）只能靠同一任务集跑两组阈值量出来，
    而"改常量再跑一次"会把代码差异混进差值里。

    ``ask`` 注入审批回调（None = 回落到 stdin）；``state`` 允许调用方传入一份
    已建好的运行状态——取消需要从另一个线程置位，所以取消路径必须能拿到它。
    传了 ``state`` 时 ``auto_approve`` / ``ask`` / ``on_event`` / ``permission_mode`` /
    ``ledger`` / ``workspace_root`` 全部以那份 state 为准（唯一权威，不做合并）。

    循环**没有轮数上限**：终止条件只有"模型不再请求工具"，以及两个取消检查点。
    这是有意的——轮数不是收敛判据，任何固定数字都会把"多读几个文件"这种普通任务
    判负（旧代码写死过 8，实测拦下过 9 个文件的简单任务）。限制留在工具层与设置层：
    单条命令的超时与输出上限（`tools/shell.py`）、工具输出进上下文前的预算
    （`runtime/hooks.py`）、以及"一步内最多并发几个可并行的调用"。
    """
    config = config or load_config()
    if config.context_window is None:
        # 环境变量与内置表都没给窗口时，问一次 provider 的 `/models`（失败回 None，
        # 进程内缓存）。放在这里是因为**所有**运行路径都经过它：CLI、Web、子 agent
        # 因此不必各接一遍。占用率缺分母只是少一个数，不该拦住任何一次运行。
        probed = fetch_context_length(config)
        if probed:
            config = replace(config, context_window=probed)
    summarize = summarize or chat
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry
    # 并发上限的唯一取值点：显式参数 > 环境变量（已由 load_config 归一进 config）。
    parallel_limit = (
        config.max_parallel_tool_calls
        if max_parallel_tools is None
        else max_parallel_tools
    )

    def emit(message: dict[str, Any]) -> None:
        if on_message is not None:
            on_message(message)

    # 注册表与 system prompt 都由 state 负责——循环不知道默认指令文案，也不持有注册表。
    state = state or RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        observer=on_event,
        permission_mode=permission_mode,
        ledger=ledger,
        security=security,
        workspace_root=workspace_root,
        hooks=hooks,
        # 窗口是模型配置的一部分，占用率的分母因此跟着 config 走（不再多一个参数）。
        context_window=config.context_window,
    )
    # 探测结果要落到 state 上：svc 的路径**先**建 state（带上 config.context_window，
    # 那一刻还是 None）**再**进循环，探测只发生在这里；不回填的话 Web 界面永远看不到
    # 占用率（CLI 那条路径因为 for_run 就在下面，天然拿得到）。
    if state.context_window is None:
        state.context_window = config.context_window

    # 上下文（system 装配、环境、技能目录、tail 块与压缩）都归 manager；循环只把
    # 这次运行的实事交给它：指令覆盖、本轮真实下发的工具清单、阈值与摘要入口。
    ctx = ContextManager(
        transcript=Transcript(messages),
        state=state,
        config=config,
        instructions=system,
        tool_names=[str(item["function"]["name"]) for item in tools],
        budget=budget,
        summarize=summarize,
    )
    transcript = ctx.transcript

    trigger = _submit_input(transcript, state, [str(item["function"]["name"]) for item in tools])
    if trigger is None:
        return ""
    index, injected = trigger
    emit(transcript.as_messages()[index])

    def note_prompt_parts(request: ComposedRequest) -> None:
        """发请求前记下三块文本的字符数。

        系统提示与工具定义**从不发给前端**（前端只有对话条目），所以"上下文被谁占了"
        只能在发请求的这一刻、由内核自己算并随快照带出去；分配成 token 由
        ``RunState.usage_report()`` 做（按字符占比，不引入绝对系数）。tail 块计入
        messages——它们确实随请求发出去了，只是不落库。
        """
        state.record_prompt_parts(
            system=request.system_chars,
            tools=len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
            messages=request.messages_chars,
        )

    # `itertools.count` 表达"轮次没有天然终点"：循环的出口只有模型的回答与取消
    # 检查点。手写状态机式的循环不行——A3 门禁要求这里保持成"一段调度"
    # （tests/test_web_boundaries.py 会读本文件做断言）。
    for round_index in itertools.count(1):
        state.round = round_index
        state.check_cancelled()  # 检查点 1：每轮开始前（§7.4）

        state.emit(events.RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

        # 上下文装配与压缩都在 compose 里：①② 每轮跑，③④ 超限时才做，
        # ④ 整个运行最多一次。injected 只在首轮生效（system 定格）。
        request = ctx.compose(injected=injected)

        # 模型调用；报上下文超限时兜底压缩并重试一次（整个运行最多一次）
        note_prompt_parts(request)
        try:
            turn = chat(
                config,
                request.messages,
                system=request.system,
                tools=tools,
                max_tokens=max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            ctx.reactive()
            # 兜底压缩改过候选消息，tail 与分块要按**这一次**的实际请求重算。
            request = ctx.render()
            note_prompt_parts(request)
            turn = chat(
                config,
                request.messages,
                system=request.system,
                tools=tools,
                max_tokens=max_tokens,
            )

        state.record_usage(turn.usage)
        transcript.append(turn.message)
        emit(turn.message)
        # usage 快照跟着**这一轮的**真实读数走（transient 事件，不进重放预算）：
        # 前端据此实时显示占用与命中；刷新后由会话里落盘的值接上。
        state.emit(
            events.RUN_STATUS,
            round=round_index,
            tokens=state.tokens,
            activity="model",
            finish_reason=turn.finish_reason,
            usage=state.usage_report(),
        )
        logger.info(
            "round=%d finish=%s tool_calls=%d tokens=%d",
            round_index,
            turn.finish_reason or "-",
            len(turn.tool_calls),
            turn.usage.total_tokens,
        )

        if not turn.tool_calls:
            # Stop：回调可以要求"先别退出"
            stop: dict[str, Any] = {
                "final_text": turn.text,
                "messages": transcript.as_messages(),
                "summary": None,
                "nudge": None,
                **state.snapshot(),
            }
            blocked = state.hooks.trigger("Stop", stop) == BLOCK
            blank = _blank_answer(turn)
            reason = _blank_reason(turn) if blank else ""
            if blank and not blocked:
                # 没有可见正文不算「答完了」：按一次 Stop 拦截处理，先补问一句。
                # 放在 Stop 之后判断，是为了不与回调的要求打架（回调要补问就用它的文案）。
                blocked = True
                stop["nudge"] = BLANK_ANSWER_NUDGE.format(reason=reason)
            if blocked and state.stop_blocks < max_stop_blocks:
                state.stop_blocks += 1
                nudge = stop.get("nudge")
                if nudge:
                    message = {"role": "user", "content": str(nudge)}
                    transcript.append(message)
                    state.emit(events.STOP_NUDGE, content=str(nudge), message=message)
                    emit(message)
                logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
                continue
            if blank:
                # 补问也补不出正文：用一条**可见**的 notice 收尾。绝不返回空串——
                # 那正是现场那条「成功但没有答复」的路径。
                notice = BLANK_ANSWER_NOTICE.format(reason=reason)
                message = {"role": "assistant", "content": notice}
                transcript.append(message)
                emit(message)
                logger.warning(
                    "仍然没有可见正文（%s；finish_reason=%s，round=%d），以 notice 收尾",
                    reason,
                    turn.finish_reason or "-",
                    round_index,
                )
                return notice
            if blocked:
                logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_stop_blocks)
            return turn.text

        state.check_cancelled()  # 检查点 2：每批工具执行前（§7.4）
        outcomes = execute_batch(
            turn.tool_calls,
            state=state,
            registry=registry,
            round_index=round_index,
            # 参数校验用**发给模型的同一份**定义，不另抄一份 schema。
            schemas={
                str(item["function"]["name"]): item["function"]["parameters"]
                for item in tools
            },
            # 批内并发：段内并发安全工具一起跑，独占调用是屏障（execution.plan_segments）。
            max_parallel=parallel_limit,
        )
        for outcome in outcomes:
            message = {
                "role": "tool",
                "tool_call_id": outcome.tool_call_id,
                "content": outcome.content,
            }
            transcript.append(message)
            emit(message)

        if state.denial_streak >= max_consecutive_denials:
            # 策略连续拒绝、期间一次都没通过：再问下去只是把同一堵墙撞 N 遍。
            # 停在这里而不是继续调度——文案走 on_message，前端照常看得见这张卡片；
            # 不新增事件类型（事件契约与前端映射都不动）。
            halt = (
                f"（运行已停止：连续 {state.denial_streak} 次工具调用被权限策略拒绝，"
                "期间没有一次通过。请向用户说明需要哪个目标或哪条命令的授权，"
                "再开新一轮。）"
            )
            logger.warning(
                "连续 %d 次工具调用被拒，运行提前结束", state.denial_streak
            )
            message = {"role": "assistant", "content": halt}
            transcript.append(message)
            emit(message)
            return halt
    # 静态检查器不认"无限 for"（`itertools.count` 在它们眼里照样会结束），所以这里
    # 必须给一个显式出口。运行期不可达：循环只从模型的 return、取消或异常退出。
    raise AssertionError("轮次循环没有正常出口")
