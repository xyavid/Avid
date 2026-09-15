"""Agent 循环。

阶段 1b 已注册第一个真实工具 read_file（见 tools.py），「模型 → 工具 → 模型」
的往返真正跑通。

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
from .llm import DEFAULT_MAX_TOKENS, Turn, chat_completion
from .permission import check_permission
from .tools import TOOL_IMPLS, TOOLS, ToolImpl

logger = logging.getLogger("avid.agent")

SYSTEM = (
    "你是 Avid，一个能自主调用工具完成任务的 agent。"
    "需要外部信息或动作时调用工具；信息足够时直接给出答案。"
)

MAX_ROUNDS = 8

# 工具调用通过权限校验与否，由这个签名决定。
CheckPermission = Callable[[str, dict[str, Any]], bool]

DENIED_CONTENT = "Permission denied."


class RoundLimitExceeded(RuntimeError):
    """连续多轮都在调用工具，未收敛。阶段 2 会把它改成可分类的终止原因。"""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _execute_one(
    name: str,
    raw_arguments: str,
    registry: dict[str, ToolImpl],
    check: CheckPermission,
) -> str:
    """解析参数 → 查 handler → 权限校验 → 执行。

    顺序是有意的：参数解析失败、工具不存在、参数不是对象，都不该弹审批——
    那不是权限问题，用户没理由被拉进来。
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

    if not check(name, arguments):
        logger.info("  ✗ 权限拒绝 %s", name)
        return DENIED_CONTENT

    try:
        return _as_text(impl(arguments))
    except Exception as exc:  # 工具失败回传模型，循环不中断
        return f"工具 {name} 执行失败：{exc}"


def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    registry: dict[str, ToolImpl],
    check: CheckPermission | None = None,
) -> list[dict[str, Any]]:
    """逐个执行工具调用，汇总为可直接追加进 messages 的 tool 消息。

    工具不存在、抛异常、参数非法、权限被拒，都变成回传给模型的文本，
    而不是中断循环。
    """
    check = check_permission if check is None else check
    results: list[dict[str, Any]] = []

    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        raw_arguments = function.get("arguments") or "{}"
        logger.info("  → %s %s", name, raw_arguments)

        content = _execute_one(name, raw_arguments, registry, check)

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
    check: CheckPermission | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_rounds: int = MAX_ROUNDS,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    messages 原地追加：每轮的 assistant 消息，以及工具结果。system 不写进 messages。
    check 为 None 时用默认的 check_permission（三闸门）。
    """
    config = config or load_config()
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry

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
            return turn.text

        messages.extend(execute_tool_calls(turn.tool_calls, registry, check))

    raise RoundLimitExceeded(f"达到轮数上限 {max_rounds}，模型仍在请求工具，未收敛")
