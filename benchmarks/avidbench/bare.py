"""基准线循环：同一个 chat、同一套工具协议，去掉全部 Avid 机制。

它是 `bare` 变体的实现，刻意写成"最朴素的 agent 循环"——读消息、调模型、
执行工具、再调模型。与 `runtime/loop.py` 的差别**逐条**是：

| 机制 | avid loop | bare loop |
|---|---|---|
| 上下文压缩（4 级管线） | 有 | 无（报上下文超限就直接失败） |
| TODO 提醒 | 有 | 无 |
| Stop nudge / 拦截重试 | 有 | 无 |
| hook（含重复调用提醒、截断落盘） | 有 | 无（空注册表） |
| 取消检查点 | 有 | 有（硬超时要能停下来） |

工具调用复用 `runtime.execution.execute_batch`——那是**工具协议**（参数校验、
权限、事件），不是被消融的机制；两边必须同协议，否则差的不只是循环。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from avid.ai.client import DEFAULT_MAX_TOKENS
from avid.runtime import events
from avid.runtime.execution import execute_batch
from avid.runtime.state import RunState


def bare_loop(
    messages: list[dict[str, Any]],
    *,
    system: str,
    tools: list[dict[str, Any]],
    registry: dict[str, Any],
    config: Any,
    chat: Callable[..., Any],
    state: RunState,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_parallel: int = 1,
    on_message: Callable[[dict[str, Any]], Any] | None = None,
) -> str:
    """跑到模型不再要工具为止。`messages` 原地更新，与 avid loop 的约定一致。

    与 avid loop 一样**没有轮数上限**——对消融实验也要公平：一边有的限制另一边不能有。
    停下来靠取消检查点，runner 的墙钟 watchdog 正是走它。

    ``max_parallel`` 缺省 1（逐个执行）：评测基线是在串行下量的，消融实验要改的也是
    "有没有那些机制"，不该顺带改掉工具派发方式。
    """

    def emit(message: dict[str, Any]) -> None:
        if on_message is not None:
            on_message(message)

    if messages and messages[-1].get("role") == "user":
        emit(messages[-1])
    schemas = {
        str(item["function"]["name"]): item["function"]["parameters"] for item in tools
    }

    for round_index in itertools.count(1):
        state.round = round_index
        state.check_cancelled()  # 检查点：硬超时靠它生效
        state.emit(events.RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

        turn = chat(
            config,
            list(messages),
            system=system,
            tools=tools,
            max_tokens=max_tokens,
        )
        state.tokens += turn.usage.total_tokens
        messages.append(turn.message)
        emit(turn.message)
        state.emit(
            events.RUN_STATUS,
            round=round_index,
            tokens=state.tokens,
            activity="model",
            finish_reason=turn.finish_reason,
        )

        if not turn.tool_calls:
            return str(turn.text)

        state.check_cancelled()  # 检查点：每批工具执行前
        outcomes = execute_batch(
            turn.tool_calls,
            state=state,
            registry=registry,
            round_index=round_index,
            schemas=schemas,
            max_parallel=max_parallel,
        )
        for outcome in outcomes:
            message = {
                "role": "tool",
                "tool_call_id": outcome.tool_call_id,
                "content": outcome.content,
            }
            messages.append(message)
            emit(message)

    # 与内核循环同形：给静态检查器一个显式出口，运行期不可达。
    raise AssertionError("轮次循环没有正常出口")
