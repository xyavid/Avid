"""工具执行环节：解析参数 → 拦截 → 执行 → 回填。

循环不认识工具协议，只知道"给我一批 ``tool_calls``，还我一批结果"。

失败一律**回文本、不抛异常**——这是本项目的既有约定（与 pi 的"工具抛异常 +
``isError: true``"相反，见 `docs/design/runtime-architecture.md` §8.2 D2）。

工具执行的三个事实（开始 / 结束 / 被拒）在这里变成事件：hook 的 context 里本来
就有它们，只是此前没有任何消费者（设计文档 §1.2、§5.3）。``tool_call_id`` 是
新加进 context 的，前端据此把「调用声明 / 开始 / 结束」缝在同一条卡片上。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..tools import ToolImpl, workspace
from . import events
from .hooks import BLOCK, brief, trigger_hooks
from .state import RunState

logger = logging.getLogger("avid.runtime.execution")

# 拦截时回传给模型的兜底文案。回调可以把 context["denied_content"] 设成
# 更有用的内容（permission_hook 就会），这里只在回调没设时使用。
DENIED_CONTENT = "Permission denied."

# 需要读 RunState 的工具。文件类工具进去是因为它们要读运行级工作区根与越界授权账本
# （``state.workspace_root`` / ``state.outside_allowed``），而这两个决定都由权限层做。
# 契约测试校验这张表里的名字都在注册表里、且这些 handler 确实接受 state 关键字。
STATEFUL_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "bash",
        "create_task",
        "update_task",
        "can_start",
        "claim_task",
        "complete_task",
        "get_task",
        "todo_write",
        "load_skill",
        "subagent",
    }
)


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
    tool_call_id: str = "",
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
    started_at = time.monotonic()

    before: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "tool_call_id": tool_call_id,
        "auto_approve": state.auto_approve,
        # 权限层的运行级上下文：模式决定"哪些动作打问号"，账本让"同意一次"生效，
        # 工作区根是越界判定的基准。根在**调用时**解析，测试的 monkeypatch 才有效。
        "permission_mode": state.permission_mode,
        "approval_ledger": state.ledger,
        "workspace_root": state.workspace_root or str(workspace.WORKSPACE_ROOT),
        # 策略注入点：hook 回调据此发起审批，而不必自己去读 stdin（§7.2）。
        "ask": state.ask,
    }
    state.emit(
        events.TOOL_CALL_STARTED,
        tool=name,
        arguments=arguments,
        round=round_index,
        tool_call_id=tool_call_id,
    )
    if trigger_hooks("PreToolUse", before) == BLOCK:
        state.denials += 1
        logger.info("  ✗ 已拦截 %s", name)
        state.emit(
            events.TOOL_CALL_DENIED,
            tool=name,
            arguments=arguments,
            round=round_index,
            tool_call_id=tool_call_id,
            kind=before.get("denied_kind") or "user",
            reason=before.get("denied_reason") or "",
        )
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
        "tool_call_id": tool_call_id,
        "content": content,
        "truncated": False,
    }
    trigger_hooks("PostToolUse", after)
    final = str(after["content"])
    state.emit(
        events.TOOL_CALL_FINISHED,
        tool=name,
        arguments=arguments,
        round=round_index,
        tool_call_id=tool_call_id,
        content=final,
        truncated=bool(after.get("truncated")),
        duration_ms=int((time.monotonic() - started_at) * 1000),
    )
    return final


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
        logger.info("  → %s %s", name, brief(raw_arguments))

        content = execute_one(
            name,
            raw_arguments,
            registry,
            state=state,
            round_index=round_index,
            tool_call_id=str(call.get("id", "")),
        )

        logger.info("  ← %s 字符", len(content))
        outcomes.append(
            ToolOutcome(tool_call_id=str(call.get("id", "")), content=content)
        )

    return outcomes
