"""Agent 循环。

扩展点全部走 hooks.py 的事件机制——循环本身不认识权限、日志、截断这些策略，
它只知道四个事件名和 context 字典的约定。

协议映射（Anthropic 语义 → OpenAI 兼容）：
  content 里的 tool_use 块        → message.tool_calls[]
  tool_result 的一条 user 消息    → 每次调用一条 {"role": "tool", ...}
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from .config import Config, load_config
from .hooks import ALLOW, BLOCK, trigger_hooks
from .llm import DEFAULT_MAX_TOKENS, Turn, chat_completion
from .tools import TOOL_IMPLS, TOOLS, ToolImpl

logger = logging.getLogger("avid.agent")

SYSTEM = (
    "你是 Avid，一个能自主调用工具完成任务的 agent。"
    "需要外部信息或动作时调用工具；信息足够时直接给出答案。"
)

MAX_ROUNDS = 8

# Stop 被拦截后最多再补几轮。防止写坏的回调把循环拖成死循环。
MAX_STOP_BLOCKS = 1

DENIED_CONTENT = "Permission denied."


class RoundLimitExceeded(RuntimeError):
    """连续多轮都在调用工具，未收敛。阶段 2 会把它改成可分类的终止原因。"""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _last_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message
    return None


def _execute_one(
    name: str,
    raw_arguments: str,
    registry: dict[str, ToolImpl],
    *,
    round_index: int,
    auto_approve: bool,
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
        "auto_approve": auto_approve,
    }
    if trigger_hooks("PreToolUse", before) == BLOCK:
        stats["denials"] += 1
        logger.info("  ✗ 已拦截 %s", name)
        return DENIED_CONTENT

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
    auto_approve: bool = False,
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
            auto_approve=auto_approve,
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
    system: str = SYSTEM,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    config: Config | None = None,
    chat: Callable[..., Turn] = chat_completion,
    auto_approve: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_rounds: int = MAX_ROUNDS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    messages 原地追加：每轮的 assistant 消息，以及工具结果。system 不写进 messages。
    """
    config = config or load_config()
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry
    stats = {"tool_calls": 0, "denials": 0}

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

    stop_blocks = 0
    for round_index in range(1, max_rounds + 1):
        turn = chat(
            config, messages, system=system, tools=tools, max_tokens=max_tokens
        )
        messages.append(turn.message)
        logger.info(
            "round=%d finish=%s tool_calls=%d tokens=%d",
            round_index,
            turn.finish_reason or "-",
            len(turn.tool_calls),
            turn.usage.total_tokens,
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
                auto_approve=auto_approve,
                stats=stats,
            )
        )

    raise RoundLimitExceeded(f"达到轮数上限 {max_rounds}，模型仍在请求工具，未收敛")
