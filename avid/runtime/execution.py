"""Tool execution: parse arguments, gate the call, run it and report the outcome as text."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any

from ..tools import ToolImpl, workspace
from ..tools.registry import specs
from ..tools.safety import is_concurrency_safe
from ..tools.validate import bad_arguments, validate_arguments
from . import events
from .hooks import BLOCK, brief
from .state import RunState

logger = logging.getLogger("avid.runtime.execution")

# Fallback text when a blocking hook does not set a more useful denied_content of its own.
DENIED_CONTENT = "Permission denied."
# Text returned when PostToolUse blocks a result, so the model does not read it as empty output.
POST_BLOCKED_CONTENT = "错误：工具结果被 PostToolUse hook 拦截，内容未进入上下文。"
# A real "not executed" answer is required: every declared call needs one response, otherwise the
# assistant message keeps an unanswered call and the transcript becomes structurally invalid.
CANCELLED_CONTENT = "错误：运行已取消，本次调用未执行。"

# Tools needing the run state, derived from the registry instead of a hand-maintained list.
STATEFUL_TOOLS: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.stateful
)


@dataclass(frozen=True)
class ToolOutcome:
    """One tool call id paired with the content handed back to the model."""

    tool_call_id: str
    content: str


def _as_text(value: Any) -> str:
    """Render a result as text, JSON-encoding anything that is not already a string."""
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
    parameters: dict[str, Any] | None = None,
    parallel: int = 1,
) -> str:
    """Run one tool call and return text for the model; failures return text rather than raising."""
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return bad_arguments(f"参数不是合法 JSON（{exc}）")

    if not isinstance(arguments, dict):
        return bad_arguments("参数必须是 JSON 对象")

    # Argument and protocol failures return without events; only policy outcomes are observable.
    impl = registry.get(name)
    if impl is None:
        return f"未知工具：{name}"

    if parameters is not None:
        # Validation runs against the schema node sent to the model, so the two cannot disagree.
        problem = validate_arguments(parameters, arguments)
        if problem is not None:
            return problem

    state.note_tool_call()
    started_at = time.monotonic()

    before: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "tool_call_id": tool_call_id,
        "auto_approve": state.auto_approve,
        # Run-level facts the permission layer reads; the workspace root is resolved at call time.
        "permission_mode": state.permission_mode,
        # The security spec travels as data, which is why this module imports nothing from policy.
        "security": state.security,
        "approval_ledger": state.ledger,
        "workspace_root": state.workspace_root or str(workspace.WORKSPACE_ROOT),
        # The approval callback is injected here so hooks never read stdin themselves.
        "ask": state.ask,
    }
    # parallel reports how many calls share this segment; it changes no execution semantics.
    state.emit(
        events.TOOL_CALL_STARTED,
        tool=name,
        arguments=arguments,
        round=round_index,
        tool_call_id=tool_call_id,
        parallel=parallel,
    )
    if state.hooks.trigger("PreToolUse", before) == BLOCK:
        state.note_denial()
        logger.info("  ✗ 已拦截 %s", name)
        state.emit(
            events.TOOL_CALL_DENIED,
            tool=name,
            arguments=arguments,
            round=round_index,
            tool_call_id=tool_call_id,
            kind=before.get("denied_kind") or "user",
            reason=before.get("denied_reason") or "",
            parallel=parallel,
        )
        # Whichever hook blocked the call decides the message; the fallback applies only
        # when none is set.
        return str(before.get("denied_content") or DENIED_CONTENT)

    # This call passed the gate, so the denial streak resets; a failing tool is not a denial.
    state.note_allowed()

    try:
        if name in STATEFUL_TOOLS:
            content = _as_text(impl(arguments, state=state))
        else:
            content = _as_text(impl(arguments))
    except Exception as exc:  # A tool failure goes back to the model and never breaks the loop
        content = (
            f"工具执行失败：{name}（{exc}）；"
            "不要用同样的参数重复调用，先检查参数与环境。"
        )

    after: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "tool_call_id": tool_call_id,
        "content": content,
        "truncated": False,
        # Spills must land in this run's workspace and carry the run tag, or runs collide.
        "workspace_root": state.workspace_root or str(workspace.WORKSPACE_ROOT),
        "run_tag": state.run_tag,
        # Repeat counting lives on the run state; the next round sees the updated counts.
        "repeat_calls": state.repeat_calls,
        # PostToolUse may run concurrently, so counting goes through the atomic increment.
        "bump_repeat": state.note_repeat,
    }
    if state.hooks.trigger("PostToolUse", after) == BLOCK:
        # A blocked PostToolUse keeps the tool's real execution but swaps what enters the context.
        after["content"] = str(after.get("denied_content") or POST_BLOCKED_CONTENT)
        after["blocked"] = True
        logger.info("  ✗ 结果被 PostToolUse 拦截 %s", name)
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
        parallel=parallel,
    )
    return final


def _call_name(call: dict[str, Any]) -> str:
    return str((call.get("function") or {}).get("name", ""))


def plan_segments(
    tool_calls: list[dict[str, Any]], max_parallel: int
) -> list[list[int]]:
    """Split a batch into ordered segments: safe calls share one, exclusive calls stand alone."""
    segments: list[list[int]] = []
    current: list[int] = []
    for index, call in enumerate(tool_calls):
        if max_parallel > 1 and is_concurrency_safe(_call_name(call)):
            current.append(index)
            continue
        if current:
            segments.append(current)
            current = []
        segments.append([index])
    if current:
        segments.append(current)
    # Cross-call data dependencies cannot be expressed in one assistant batch, so only category
    # order is enforced: a write splits the batch, which keeps earlier reads ahead of it.
    return segments


def execute_batch(
    tool_calls: list[dict[str, Any]],
    *,
    state: RunState,
    registry: dict[str, ToolImpl],
    round_index: int = 0,
    schemas: dict[str, dict[str, Any]] | None = None,
    max_parallel: int = 1,
) -> list[ToolOutcome]:
    """Execute a batch and return outcomes in assistant source order, never completion order."""
    limit = max(1, int(max_parallel))
    outcomes: list[ToolOutcome | None] = [None] * len(tool_calls)
    schemas = schemas or {}

    def run(index: int, *, width: int) -> str:
        # A segment is dispatched as a whole, so cancellation can land while a call is still queued.
        if state.cancelled:
            return CANCELLED_CONTENT
        call = tool_calls[index]
        name = _call_name(call)
        raw_arguments = (call.get("function") or {}).get("arguments") or "{}"
        logger.info("  → %s %s", name, brief(raw_arguments))
        try:
            content = execute_one(
                name,
                raw_arguments,
                registry,
                state=state,
                round_index=round_index,
                tool_call_id=str(call.get("id", "")),
                parameters=schemas.get(name),
                parallel=width,
            )
        except Exception as exc:  # Execution must not leak an exception to its batch siblings
            logger.exception("工具调用 %s 抛出未预期异常，按失败回传", name)
            content = f"工具执行失败：{name}（{exc}）；不要用同样的参数重复调用。"
        logger.info("  ← %s 字符", len(content))
        return content

    def store(index: int, content: str) -> None:
        outcomes[index] = ToolOutcome(
            tool_call_id=str(tool_calls[index].get("id", "")), content=content
        )

    for segment in plan_segments(tool_calls, limit):
        if state.cancelled:
            # Nothing is dispatched and the rest answer "not executed", keeping the batch complete.
            for index in segment:
                store(index, CANCELLED_CONTENT)
            continue
        if len(segment) == 1:
            store(segment[0], run(segment[0], width=1))
            continue
        width = min(len(segment), limit)
        logger.info("并行执行 %d 个调用（并发上限 %d）", len(segment), width)
        with ThreadPoolExecutor(max_workers=width) as pool:
            # partial rather than a lambda: a closure would capture the segment loop variable.
            results = list(pool.map(partial(run, width=width), segment))
        for index, content in zip(segment, results, strict=True):
            store(index, content)

    # Segments partition the batch, so every index must have received an outcome.
    missing = [index for index, item in enumerate(outcomes) if item is None]
    if missing:  # pragma: no cover - a correct partition cannot leave a gap
        raise AssertionError(f"批内调用 {missing} 没有结果，段划分不完整")
    return [item for item in outcomes if item is not None]
