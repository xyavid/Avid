"""工具执行环节：解析参数 → 拦截 → 执行 → 回填。

循环不认识工具协议，只知道"给我一批 ``tool_calls``，还我一批结果"。

失败一律**回文本、不抛异常**——这是本项目的既有约定（与 pi 的"工具抛异常 +
``isError: true``"相反，见 `docs/design/runtime-architecture.md` §8.2 D2）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .hooks import BLOCK, trigger_hooks
from .state import RunState
from .tools import ToolImpl

logger = logging.getLogger("avid.execution")

# 拦截时回传给模型的兜底文案。回调可以把 context["denied_content"] 设成
# 更有用的内容（permission_hook 就会），这里只在回调没设时使用。
DENIED_CONTENT = "Permission denied."

# 需要读 RunState 的工具。新增这类工具时同时改这里——契约测试会校验它
# 与工具注册表一致、且这些 handler 确实接受 state 参数。
STATEFUL_TOOLS: frozenset[str] = frozenset({"todo_write", "load_skill", "subagent"})


@dataclass(frozen=True)
class ToolOutcome:
    tool_call_id: str
    content: str


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def execute_one(
    name: str,
    raw_arguments: str,
    registry: dict[str, ToolImpl],
    *,
    state: RunState,
    round_index: int,
) -> str:
    """执行一次工具调用，返回要回传给模型的内容。

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

    state.tool_calls += 1

    before: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "auto_approve": state.auto_approve,
    }
    if trigger_hooks("PreToolUse", before) == BLOCK:
        state.denials += 1
        logger.info("  ✗ 已拦截 %s", name)
        # 文案由拦截它的回调决定；回调没说就用兜底值。
        return str(before.get("denied_content") or DENIED_CONTENT)

    try:
        if name in STATEFUL_TOOLS:
            content = _as_text(impl(arguments, state=state))
        else:
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


def execute_batch(
    tool_calls: list[dict[str, Any]],
    *,
    state: RunState,
    registry: dict[str, ToolImpl],
    round_index: int = 0,
) -> list[ToolOutcome]:
    """逐个执行，按 assistant 源顺序返回结果。"""
    outcomes: list[ToolOutcome] = []

    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        raw_arguments = function.get("arguments") or "{}"
        logger.info("  → %s %s", name, raw_arguments)

        content = execute_one(
            name, raw_arguments, registry, state=state, round_index=round_index
        )

        logger.info("  ← %s 字符", len(content))
        outcomes.append(
            ToolOutcome(tool_call_id=str(call.get("id", "")), content=content)
        )

    return outcomes
