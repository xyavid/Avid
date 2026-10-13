"""What the loop itself enforces at its four points: the permission gate, the repeat reminder, the
tool-output budget and the run summary.

These are plain functions, not hooks: hooks are for extra user configuration, so nothing the body
enforces may sit behind a registry a caller can replace.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..security.permission import always_allow, brokerize, decide
from .compaction import spill
from .tools import workspace

if TYPE_CHECKING:
    from .state import RunState

logger = logging.getLogger("avid.agent.guards")

# Cap on tool output entering the context; the overflow is spilled to disk as an excerpt.
MAX_TOOL_OUTPUT_CHARS = 8000
# Reminder thresholds; the first two repetitions are ordinary retry behaviour.
REPEAT_REMIND_AT: tuple[int, ...] = (3, 5)

# Argument keys whose values are masked before they can reach the logs.
_SECRET_KEYS = ("token", "api_key", "apikey", "authorization", "password", "secret", "key")
# Inline credential forms: name=value and name: value, with an optional scheme word first.
_SECRET_IN_TEXT = re.compile(
    r"(?i)((?:authorization|token|api[_-]?key|password|secret)\s*[=:]\s*)"
    r"(?:bearer\s+)?\S+"
)
# Bare bearer tokens need their own pattern, since the one above requires a key name.
_SECRET_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_BRIEF_LIMIT = 300  # characters kept from a log summary before it is truncated


@dataclass(frozen=True)
class Denial:
    """Why a call was refused: the kind the event carries, the reason and the text the model reads."""

    kind: str
    reason: str
    content: str


def brief(arguments: Any, *, limit: int = _BRIEF_LIMIT) -> str:
    """Return a log-safe argument summary with credentials masked and the text truncated."""

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    "***"
                    if any(mark in str(key).lower() for mark in _SECRET_KEYS)
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return _SECRET_IN_TEXT.sub(
                r"\1***", _SECRET_BEARER.sub(r"\1***", value)
            )
        return value

    # Masking is deliberately generous: the logs exist to locate problems, not to archive commands.
    text = json.dumps(redact(arguments), ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def check_permission(
    state: "RunState", *, tool: str, arguments: dict[str, Any]
) -> Denial | None:
    """Broker the call, decide it, audit the verdict and explain a denial.

    Auto-approve only swaps the answerer; it does not change which calls are questioned, and the
    credential refusal survives it.
    """
    security = state.security
    action = brokerize(
        tool, arguments, root=state.workspace_root or str(workspace.WORKSPACE_ROOT)
    )
    answerer = always_allow if state.auto_approve else state.ask

    decision = decide(
        action,
        full=bool(security.full) if security is not None else False,
        ledger=state.ledger,
        ask=answerer,
    )

    # Allows are audited as well, otherwise nothing records what this run actually let through.
    if security is not None:
        security.audit_write(
            "decision",
            tool=tool,
            command=action.normalized or None,
            targets=list(action.targets),
            outside=list(action.outside),
            risks=list(action.risks),
            capabilities=sorted(action.capabilities),
            network=action.network or None,
            network_target=action.network_target if action.network else None,
            decision_type=decision.type,
            verdict=decision.verdict,
            decision_kind=decision.kind or None,
            danger=action.damage or None,
            answered_by=decision.answered_by or None,
            key=list(decision.key) if decision.key else None,
            reason=decision.reason or None,
        )

    if decision.allowed:
        return None
    # Denials carry a per-category reason, because a generic message makes the model retry blindly.
    return Denial(
        kind=decision.kind or "user",
        reason=f"{tool}：{decision.reason}",
        content=decision.message,
    )


def note_repeat(
    state: "RunState", *, tool: str, arguments: dict[str, Any], content: str
) -> str:
    """Append the reminder on the 3rd and 5th identical call; the previous output stays in full.

    The count lives on the run state and is bumped atomically, since PostToolUse runs concurrently
    for a parallel batch.
    """
    # Normalized JSON makes the comparison independent of key order and formatting.
    key = f"{tool}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)}"
    times = state.note_repeat(key)
    if times not in REPEAT_REMIND_AT:
        return content
    return content + (
        f"\n\n[重复调用提醒] 同一次调用（{tool} + 相同参数）已经重复 {times} 次。"
        "先看上一次的结果再决定：换一种做法，或者如果已经做完就收尾。"
    )


def fit_tool_output(state: "RunState", *, content: str) -> tuple[str, bool]:
    """Fit one tool result into the context budget, returning (content, truncated).

    Over the cap the full text spills to disk and the context keeps head and tail excerpts; a
    failed spill downgrades this to a head-only truncation and never drops the result.
    """
    if len(content) <= MAX_TOOL_OUTPUT_CHARS:
        return content, False

    original = len(content)
    path = spill(
        content,
        "tool-output",
        Path(state.workspace_root) if state.workspace_root else None,
        state.run_tag,
    )
    if path is not None:
        notice = (
            f"\n…（已按上下文预算截断，原文 {original} 字符，已存至 {path}；"
            "需要时用 read_file 读回。以下为首尾节选）\n"
        )
    else:
        notice = f"\n…（已按上下文预算截断，原文 {original} 字符，上限 {MAX_TOOL_OUTPUT_CHARS}）"

    if len(notice) >= MAX_TOOL_OUTPUT_CHARS:
        return notice[:MAX_TOOL_OUTPUT_CHARS], True

    # The notice is charged to the same budget, otherwise the cap would be silently exceeded.
    keep = MAX_TOOL_OUTPUT_CHARS - len(notice)
    head = keep // 2
    return content[:head] + notice + content[original - (keep - head) :], True


def summarize_run(state: "RunState") -> str:
    """The run's own closing tally, logged once per run."""
    summary = (
        f"轮数={state.round} 工具调用={state.tool_calls} 拒绝={state.denials}"
    )
    logger.info("运行汇总：%s", summary)
    return summary
