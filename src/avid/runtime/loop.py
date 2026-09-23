"""Agent 循环：只表达调度顺序。

它不认识阈值、文案与协议细节——

* 消息结构与不变量：``ai/transcript.py``
* 运行状态与一次性标志：``runtime/state.py``
* 压缩编排：``runtime/context.py``
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
from . import context, events
from .events import RunObserver
from .execution import execute_batch
from .hooks import BLOCK, HookRegistry
from .state import TODO_REMINDER_AFTER_ROUNDS, RunState

if TYPE_CHECKING:  # 只有类型标注用它：注解是惰性的，运行时不必跨层 import 策略层
    from ..policy.permission import ApprovalLedger, AskUser

logger = logging.getLogger("avid.runtime.loop")

# Stop 被拦截后最多再补几轮。防止写坏的回调把循环拖成死循环。
MAX_STOP_BLOCKS = 1


class RoundLimitExceeded(RuntimeError):
    """显式配置的轮数上限被耗尽（`AVID_MAX_ROUNDS` 或 ``max_rounds=``）。

    缺省**不会**发生：循环的终止条件是模型不再请求工具。这个异常只在有人主动设了
    成本闸门时出现，svc 把它映射成"未完成"而不是内部错误。
    """


class RunCancelled(RuntimeError):
    """运行被显式取消（与 ``RoundLimitExceeded`` 同类，是终止原因而不是错误）。

    它只在**步骤边界**抛出：在飞的模型调用或工具调用结束后（§7.4）。已产生
    的消息在产生时就已落库，所以取消不需要补偿写，也不产生伪造的工具结果
    （不变量 I9）。
    """


def _calls_todo_write(tool_calls: list[dict[str, Any]]) -> bool:
    """本轮是否更新过 TODO —— 用来决定提醒计数是归零还是累加。"""
    return any(
        (call.get("function") or {}).get("name") == "todo_write" for call in tool_calls
    )


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
    budget: context.ContextBudget | None = None,
    auto_approve: bool = False,
    # 权限模式（strict / workspace / system）与"同意一次"账本。None 交给 RunState
    # 取默认（循环不认识策略层的默认值）。账本由调用方传入时与子 agent 共用，
    # 于是同一项操作的同意覆盖整个运行。
    permission_mode: str | None = None,
    ledger: "ApprovalLedger | None" = None,
    workspace_root: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    # 轮数上限。None = 用 `config.max_rounds`（环境变量 `AVID_MAX_ROUNDS`），而它
    # 缺省也是 None —— 也就是**无上限**。显式传正整数才是一道闸门，用于评测这类
    # 必须固定预算的场合；普通任务不该因为"多读几个文件"被判失败。
    max_rounds: int | None = None,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    todo_reminder_after: int = TODO_REMINDER_AFTER_ROUNDS,
    on_message: Callable[[dict[str, Any]], Any] | None = None,
    ask: AskUser | None = None,
    on_event: RunObserver | None = None,
    state: RunState | None = None,
    hooks: HookRegistry | None = None,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    ``messages`` 原地更新：每轮的 assistant 消息与工具结果都会写回同一个 list。
    ``system`` 是「固定指令部分」，技能目录由 SkillLoader 统一追加。

    ``on_message`` 是循环**唯一的消息通道**：本次运行产生或改写的每条消息按
    发生顺序回调一次——先是触发用户消息（UserPromptSubmit 注入**之后**的版本），
    然后是每轮追加的 assistant、工具结果、注入的 TODO 提醒与 Stop nudge。
    循环不 import 会话层，落库与否由回调决定（不变量 I7）。

    ``on_event`` 是**步骤级事实**的通道（轮次、TODO 提醒、Stop nudge、取消），
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

    ``max_rounds`` 是**可选的**成本闸门：None 表示用 ``config.max_rounds``（环境变量
    ``AVID_MAX_ROUNDS``），而它缺省同样是 None = 无上限。缺省无上限是有意的：终止条件
    是"模型不再请求工具"，一个写死的轮数会把普通任务（多读几个文件、多跑几步搜索）
    变成 ``RoundLimitExceeded``。需要固定预算的场合（评测、子 agent 成本控制）显式传值。
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
    # 上限的唯一取值点：显式参数 > 环境变量（已由 load_config 归一进 config）> 无上限。
    # 归一成 int | None 再进循环，循环里就只有一个判断，不必知道它从哪来。
    round_limit = config.max_rounds if max_rounds is None else max_rounds

    def emit(message: dict[str, Any]) -> None:
        if on_message is not None:
            on_message(message)

    transcript = Transcript(messages)
    # 注册表与 system prompt 都由 state 负责——循环不知道默认指令文案，也不持有注册表。
    state = state or RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        observer=on_event,
        permission_mode=permission_mode,
        ledger=ledger,
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

    system_prompt = state.system_prompt(system)

    trigger = _submit_input(transcript, state, [str(item["function"]["name"]) for item in tools])
    if trigger is None:
        return ""
    index, injected = trigger
    if injected:
        # 注入的上下文进**系统提示词**（每轮重建，不落库），不改写用户消息：
        # 用户消息就是用户写的那句话，落库、事件与界面都保持它原样。
        system_prompt = f"{system_prompt}\n\n" + "\n".join(injected)
    emit(transcript.as_messages()[index])

    def note_prompt_parts() -> None:
        """发请求前记下三块文本的字符数。

        系统提示与工具定义**从不发给前端**（前端只有对话条目），所以"上下文被谁占了"
        只能在发请求的这一刻、由内核自己算并随快照带出去；分配成 token 由
        ``RunState.usage_report()`` 做（按字符占比，不引入绝对系数）。
        """
        state.record_prompt_parts(
            system=len(system_prompt),
            tools=len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
            messages=transcript.estimate_chars(),
        )

    # `itertools.count` 表达"轮次没有天然终点"：上限缺席时它就是不封顶的序列，
    # 循环的出口只有模型的回答与取消检查点。手写状态机式的循环不行——A3 门禁要求
    # 这里保持成"一段调度"（tests/test_web_boundaries.py 会读本文件做断言）。
    for round_index in itertools.count(1):
        # 只在一道闸门**确实**存在时才检查它；round_limit 为 None 就没有这个分支。
        if round_limit is not None and round_index > round_limit:
            raise RoundLimitExceeded(
                f"达到轮数上限 {round_limit}（max_rounds 或 AVID_MAX_ROUNDS 指定），"
                "模型仍在请求工具，未收敛"
            )
        state.round = round_index
        state.check_cancelled()  # 检查点 1：每轮开始前（§7.4）

        # TODO 提醒依赖"第几轮"，这确实是循环自身的事实；
        # 但"该不该提醒、提醒什么"由 state 决定，循环只负责追加。
        reminder = state.todo_reminder(todo_reminder_after)
        if reminder is not None:
            message = {"role": "user", "content": reminder}
            transcript.append(message)
            # 先生成事件，再发消息：svc 据此把这条 user 消息认成 TODO 提醒，
            # 而不是用户输入（避免靠解析「[提醒]」文本前缀分类，§5.3）。
            state.emit(events.TODO_REMINDER, content=reminder, message=message)
            emit(message)
            logger.info("注入 TODO 提醒（连续 %d 轮未更新）", state.rounds_since_todo)

        state.emit(events.RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

        # 上下文管线：①② 每轮跑，③④ 超限时才跑，④ 整个运行最多一次
        context.prepare(transcript, state, config=config, summarize=summarize, budget=budget)

        # 模型调用；报上下文超限时兜底压缩并重试一次（整个运行最多一次）
        note_prompt_parts()
        try:
            turn = chat(
                config,
                transcript.as_messages(),
                system=system_prompt,
                tools=tools,
                max_tokens=max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            context.reactive(transcript, state, config=config, summarize=summarize, budget=budget)
            # 兜底压缩改过候选消息，分块要按**这一次**的实际请求重算。
            note_prompt_parts()
            turn = chat(
                config,
                transcript.as_messages(),
                system=system_prompt,
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

        state.rounds_since_todo = (
            0 if _calls_todo_write(turn.tool_calls) else state.rounds_since_todo + 1
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
        )
        for outcome in outcomes:
            message = {
                "role": "tool",
                "tool_call_id": outcome.tool_call_id,
                "content": outcome.content,
            }
            transcript.append(message)
            emit(message)
