"""Agent 循环。

扩展点全部走 hooks.py 的事件机制——循环本身不认识权限、日志、截断这些策略，
它只知道四个事件名和 context 字典的约定。

唯一的例外是 TODO 提醒：计数与注入按需求放在循环里（"第几轮"是循环自身的事实），
但提醒文案与状态模型都在 tools/todo.py，改文案不用碰循环。

上下文压缩管线（compact.py）按代价从低到高接在每轮开头：①② 每轮无条件跑且不调模型，
③④ 只在超限时跑，⑤ 在模型报超限时兜底并重试一次。

协议映射（Anthropic 语义 → OpenAI 兼容）：
  content 里的 tool_use 块        → message.tool_calls[]
  tool_result 的一条 user 消息    → 每次调用一条 {"role": "tool", ...}
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from .compact import (
    CONTEXT_CHAR_LIMIT,
    CompactReport,
    compact_history,
    estimate_chars,
    micro_compact,
    reactive_compact,
    snip_compact,
    tool_result_budget,
)
from .config import Config, load_config
from .hooks import BLOCK, trigger_hooks
from .llm import DEFAULT_MAX_TOKENS, PromptTooLongError, Turn, chat_completion
from .permission import bind_auto_approve
from .skill_loader import AGENT_INSTRUCTIONS, SkillLoader, bind_skills
from .tools import TOOL_IMPLS, TOOLS, ToolImpl
from .tools.todo import TODO_REMINDER_AFTER_ROUNDS, TodoList, bind, build_reminder

logger = logging.getLogger("avid.agent")

MAX_ROUNDS = 8

# Stop 被拦截后最多再补几轮。防止写坏的回调把循环拖成死循环。
MAX_STOP_BLOCKS = 1

# 拦截时回传给模型的兜底文案。回调可以把 context["denied_content"] 设成
# 更有用的内容（permission_hook 就会），这里只在回调没设时使用。
DENIED_CONTENT = "Permission denied."


class RoundLimitExceeded(RuntimeError):
    """连续多轮都在调用工具，未收敛。后续会把它改成可分类的终止原因。"""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _last_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message
    return None


def _calls_todo_write(tool_calls: list[dict[str, Any]]) -> bool:
    """本轮是否更新过 TODO —— 用来决定提醒计数是归零还是累加。"""
    return any(
        (call.get("function") or {}).get("name") == "todo_write" for call in tool_calls
    )


def _report(report: CompactReport | None, stats: dict[str, int]) -> None:
    """压缩发生时留下可观察的记录：一条日志 + 一个计数。"""
    if report is None:
        return
    logger.info("compact: %s", report.describe())
    stats["compactions"] += 1


def _execute_one(
    name: str,
    raw_arguments: str,
    registry: dict[str, ToolImpl],
    *,
    round_index: int,
    stats: dict[str, int],
) -> str:
    """解析参数 → 查 handler → PreToolUse → 执行 → PostToolUse。

    前两步失败（参数不是合法 JSON、参数不是对象、工具不存在）都不触发事件——
    那是协议错误，不是策略问题。
    """
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return f"参数不是合法 JSON：{exc}"

    if not isinstance(arguments, dict):
        return "错误：参数必须是 JSON 对象"

    impl = registry.get(name)
    if impl is None:
        return f"未知工具：{name}"

    stats["tool_calls"] += 1

    before: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
    }
    if trigger_hooks("PreToolUse", before) == BLOCK:
        stats["denials"] += 1
        logger.info("  ✗ 已拦截 %s", name)
        # 文案由拦截它的回调决定；回调没说就用兜底值。
        return str(before.get("denied_content") or DENIED_CONTENT)

    try:
        content = _as_text(impl(arguments))
    except Exception as exc:  # 工具失败回传模型，循环不中断
        content = f"工具 {name} 执行失败：{exc}"

    after: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "content": content,
        "truncated": False,
    }
    trigger_hooks("PostToolUse", after)
    return str(after["content"])


def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    registry: dict[str, ToolImpl],
    *,
    round_index: int = 0,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """逐个执行工具调用，汇总为可直接追加进 messages 的 tool 消息。

    工具不存在、抛异常、参数非法、被 hook 拦截，都变成回传给模型的文本，
    而不是中断循环。
    """
    stats = {"tool_calls": 0, "denials": 0} if stats is None else stats
    results: list[dict[str, Any]] = []

    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        raw_arguments = function.get("arguments") or "{}"
        logger.info("  → %s %s", name, raw_arguments)

        content = _execute_one(
            name,
            raw_arguments,
            registry,
            round_index=round_index,
            stats=stats,
        )

        logger.info("  ← %s 字符", len(content))
        results.append(
            {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}
        )

    return results


def agent_loop(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    config: Config | None = None,
    chat: Callable[..., Turn] = chat_completion,
    auto_approve: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_rounds: int = MAX_ROUNDS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    todo_reminder_after: int = TODO_REMINDER_AFTER_ROUNDS,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    messages 原地追加：每轮的 assistant 消息，以及工具结果。system 不写进 messages。
    """
    config = config or load_config()
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry
    stats = {"tool_calls": 0, "denials": 0, "compactions": 0}

    # ① UserPromptSubmit：可注入上下文，也可拦截整个输入
    submit_message = _last_user_message(messages)
    if submit_message is not None:
        submit: dict[str, Any] = {
            "prompt": str(submit_message.get("content", "")),
            "messages": messages,
            "injected": [],
        }
        if trigger_hooks("UserPromptSubmit", submit) == BLOCK:
            logger.warning("UserPromptSubmit 被拦截，未调用模型")
            return ""
        if submit["injected"]:
            submit_message["content"] = (
                "\n".join(str(item) for item in submit["injected"])
                + "\n\n"
                + submit["prompt"]
            )

    todo = TodoList()
    rounds_since_todo = 0

    # 每次运行重新扫描技能目录：磁盘变了，下一次运行的 system prompt 就是新的。
    loader = SkillLoader().scan()
    system_prompt = loader.build_system_prompt(system or AGENT_INSTRUCTIONS)
    compacted = False  # ④ 自动压缩：整个运行最多一次
    retried = False  # ⑤ 兜底重试：整个运行最多一次

    # auto_approve 是整次运行的性质，用 ContextVar 传递而不是逐个调用塞字段。
    with bind_auto_approve(auto_approve), bind(todo), bind_skills(loader):
        stop_blocks = 0
        for round_index in range(1, max_rounds + 1):
            if rounds_since_todo == todo_reminder_after:
                messages.append(
                    {"role": "user", "content": build_reminder(todo, rounds_since_todo)}
                )
                logger.info("注入 TODO 提醒（连续 %d 轮未更新）", rounds_since_todo)

            # ①② 每轮都跑，不花模型调用
            _report(tool_result_budget(messages), stats)
            _report(snip_compact(messages), stats)

            # ③④ 只在超限时。③ 免费，先做；做完仍超限才付一次摘要调用。
            if estimate_chars(messages) > CONTEXT_CHAR_LIMIT:
                _report(micro_compact(messages), stats)
            if estimate_chars(messages) > CONTEXT_CHAR_LIMIT:
                if compacted:
                    logger.info("compact: 自动压缩本运行已用过一次，跳过")
                else:
                    report = compact_history(messages, config=config, chat=chat)
                    if report is not None:
                        compacted = True
                    _report(report, stats)

            # ⑤ 兜底：模型已明确报超限时整理更早历史，然后重试一次
            try:
                turn = chat(
                    config,
                    messages,
                    system=system_prompt,
                    tools=tools,
                    max_tokens=max_tokens,
                )
            except PromptTooLongError:
                if retried:
                    raise
                retried = True
                logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
                _report(reactive_compact(messages, config=config, chat=chat), stats)
                turn = chat(
                    config,
                    messages,
                    system=system_prompt,
                    tools=tools,
                    max_tokens=max_tokens,
                )
            messages.append(turn.message)
            logger.info(
                "round=%d finish=%s tool_calls=%d tokens=%d",
                round_index,
                turn.finish_reason or "-",
                len(turn.tool_calls),
                turn.usage.total_tokens,
            )

            rounds_since_todo = (
                0 if _calls_todo_write(turn.tool_calls) else rounds_since_todo + 1
            )

            if not turn.tool_calls:
                # ④ Stop：回调可以要求"先别退出"
                stop: dict[str, Any] = {
                    "final_text": turn.text,
                    "rounds": round_index,
                    "messages": messages,
                    "summary": None,
                    "nudge": None,
                    "tool_calls": stats["tool_calls"],
                    "denials": stats["denials"],
                    "compactions": stats["compactions"],
                }
                blocked = trigger_hooks("Stop", stop) == BLOCK
                if blocked and stop_blocks < max_stop_blocks:
                    stop_blocks += 1
                    nudge = stop.get("nudge")
                    if nudge:
                        messages.append({"role": "user", "content": str(nudge)})
                    logger.info("Stop 被拦截（第 %d 次），继续循环", stop_blocks)
                    continue
                if blocked:
                    logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_stop_blocks)
                return turn.text

            # ② PreToolUse / ③ PostToolUse 在 execute_tool_calls 里触发
            messages.extend(
                execute_tool_calls(
                    turn.tool_calls,
                    registry,
                    round_index=round_index,
                    stats=stats,
                )
            )

        raise RoundLimitExceeded(
            f"达到轮数上限 {max_rounds}，模型仍在请求工具，未收敛"
        )
