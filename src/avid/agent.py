"""Agent 循环骨架。

阶段 1a 不注册任何工具：TOOLS 与 TOOL_IMPLS 都是空的，所以首轮响应不可能
出现 tool_calls，循环一轮即走到「无工具调用 → 返回」。工具分支的结构已就位，
由 tests/test_agent.py 里的假工具覆盖；真实工具在 1b 接入 read_file 时注册。

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

logger = logging.getLogger("avid.agent")

SYSTEM = (
    "你是 Avid，一个能自主调用工具完成任务的 agent。"
    "需要外部信息或动作时调用工具；信息足够时直接给出答案。"
)

# 本轮不注册任何工具。1b 接入 read_file 时在此追加定义。
TOOLS: list[dict[str, Any]] = []

ToolImpl = Callable[[dict[str, Any]], Any]

# 工具名 → 执行函数。本轮为空，任何工具调用都会回传「未知工具」。
TOOL_IMPLS: dict[str, ToolImpl] = {}

MAX_ROUNDS = 8


class RoundLimitExceeded(RuntimeError):
    """连续多轮都在调用工具，未收敛。阶段 2 会把它改成可分类的终止原因。"""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    registry: dict[str, ToolImpl],
) -> list[dict[str, Any]]:
    """逐个执行工具调用，汇总为可直接追加进 messages 的 tool 消息。

    工具不存在、抛异常、参数不是合法 JSON，都变成回传给模型的文本，
    而不是中断循环。
    """
    results: list[dict[str, Any]] = []

    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        raw_arguments = function.get("arguments") or "{}"

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            content = f"参数不是合法 JSON：{exc}"
        else:
            impl = registry.get(name)
            if impl is None:
                content = f"未知工具：{name}"
            else:
                try:
                    content = _as_text(impl(arguments))
                except Exception as exc:  # 工具失败回传模型，循环不中断
                    content = f"工具 {name} 执行失败：{exc}"

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
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_rounds: int = MAX_ROUNDS,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    messages 原地追加：每轮的 assistant 消息，以及工具结果。system 不写进 messages。
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

        messages.extend(execute_tool_calls(turn.tool_calls, registry))

    raise RoundLimitExceeded(f"连续 {max_rounds} 轮都在调用工具，未收敛")
