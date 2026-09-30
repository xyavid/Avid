"""Five-step context compaction ladder, ordered from the cheapest mechanism to the most expensive."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ai.client import LLMError, chat_completion
from ..ai.config import Config
from ..ai.transcript import Transcript

logger = logging.getLogger("avid.policy.compaction")

# Thresholds are centralized here so that tuning after measurement touches only these numbers.
TOOL_RESULT_CHAR_BUDGET = 200_000  # characters of tool results allowed before spilling the largest
TOOL_RESULT_KEEP_RECENT = 3
MAX_MESSAGES = 50
SNIP_KEEP_HEAD = 8
SNIP_KEEP_TAIL = 24
CONTEXT_CHAR_LIMIT = 400_000  # fallback conversation budget when no window reading is available
MICRO_COMPACT_KEEP_RECENT = 3
MICRO_COMPACT_TARGET_RATIO = 0.8  # fraction of the limit that the spill loop aims to reach
REACTIVE_KEEP_RECENT = 5

# Fraction of the model's real context window at which the two context-limit steps trigger.
WINDOW_TRIGGER_RATIO = 0.8
# Plausible band for measured chars per token: a reading outside it is ignored rather than followed.
MIN_CHARS_PER_TOKEN = 0.5
MAX_CHARS_PER_TOKEN = 6.0

# Spilled files must stay inside the workspace, since the read tool only reads there.
SPILL_DIR = ".avid/context"
# Marker for content that has already been spilled, so it is never spilled a second time.
SPILL_PREFIX = "[已落盘]"

SUMMARY_SYSTEM = (
    "你是上下文压缩器。把给定的对话记录压缩成一份要点摘要，供另一个 agent 接着干活。"
    "必须保留：任务目标、已确认的事实与结论、改动过的文件与关键位置、"
    "试过并已排除的做法、以及当前进行到哪一步、下一步该做什么。"
    "可以丢掉：完整文件内容、完整命令输出、重复的中间推理。"
    "只输出摘要正文。"
)

# A per-process tag keeps spill filenames unique across restarts, so a summary's read-back path stays
# valid instead of being silently overwritten by the next run.
_PROCESS_TAG = f"{os.getpid():x}{int(time.time() * 1000) & 0xFFFFF:05x}"
# In-process spill counter; it resets on restart, which is exactly why the tag above exists.
_spill_seq = 0
_SPILL_LOCK = threading.Lock()


def _next_spill_path(root: Path, kind: str, suffix: str, tag: str = "") -> Path:
    """Return the next spill path, tagged per run when the caller supplies one."""
    global _spill_seq
    with _SPILL_LOCK:  # parallel subagents compact at once, so the sequence must be taken atomically
        _spill_seq += 1
        seq = _spill_seq
    return root / f"{kind}-{tag or _PROCESS_TAG}-{seq:04d}{suffix}"


@dataclass(frozen=True)
class CompactReport:
    """What one compaction step did, logged and counted by the loop."""

    step: str
    detail: str
    before: int
    after: int

    def describe(self) -> str:
        return f"{self.step} — {self.detail}"


def derived_context_chars(
    *,
    window: int | None,
    prompt_tokens: int | None,
    chars: tuple[int, int, int] | None,
    ratio: float = WINDOW_TRIGGER_RATIO,
) -> tuple[int, float] | None:
    """Derive a conversation character budget from the model window and this session's token reading."""
    # The rate is measured from this session's own text, so mixed languages need no fixed coefficient.
    # Any missing prerequisite returns None so the caller falls back to CONTEXT_CHAR_LIMIT.
    if window is None or window <= 0:
        return None
    if chars is None:
        return None
    total = sum(chars)
    if total <= 0:
        return None
    if prompt_tokens is None or prompt_tokens <= 0:
        return None

    # Clamping keeps an implausible reading from compacting far too hard or never at all.
    per_token = min(
        max(total / prompt_tokens, MIN_CHARS_PER_TOKEN), MAX_CHARS_PER_TOKEN
    )
    # The system prompt and tool definitions are subtracted: the budget covers messages only.
    limit = int(window * ratio * per_token) - (chars[0] + chars[1])
    return max(1, limit), per_token


def _spill_root(root: Path | None = None) -> Path:
    """Return the spill directory under the run workspace root, falling back to the process root."""
    if root is not None:
        return Path(root) / SPILL_DIR
    from ..tools import workspace

    return Path(workspace.WORKSPACE_ROOT) / SPILL_DIR


# The tool layer reuses this for output truncation, so the model needs one recovery procedure only.
def spill(text: str, kind: str, root: Path | None = None, tag: str = "") -> str | None:
    """Write text under the spill directory and return its workspace-relative path, or None on failure."""
    root = _spill_root(root)
    path = _next_spill_path(root, kind, ".txt", tag)
    try:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        logger.warning("compact: 落盘失败，本次跳过")
        return None
    return f"{SPILL_DIR}/{path.name}"


def spill_notice(path: str, size: int, kind: str) -> str:
    """Render the replacement text left where spilled content used to be."""
    return f"{SPILL_PREFIX} 原{kind}共 {size} 字符，已存至 {path}；需要时用 read_file 读回。"


def _is_spilled(content: str) -> bool:
    return content.startswith(SPILL_PREFIX)


def _save_transcript(
    messages: list[dict[str, Any]], workdir: Path | None = None, tag: str = ""
) -> str:
    """Write the full transcript as JSON and return its path, or a placeholder when saving failed."""
    root = _spill_root(workdir)
    path = _next_spill_path(root, "transcript", ".json", tag)
    try:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("compact: transcript 落盘失败")
        return "（落盘失败）"
    return f"{SPILL_DIR}/{path.name}"


def _summarize(
    messages: list[dict[str, Any]], *, config: Config, chat: Any
) -> str | None:
    request = list(messages) + [
        {"role": "user", "content": "请把以上对话压缩成要点摘要。"}
    ]
    try:
        # No max_tokens is sent: reasoning tokens share that budget and a hard cap yields an empty
        # summary, which would silently turn compaction into keeping the old history.
        turn = chat(config, request, system=SUMMARY_SYSTEM)
    except (LLMError, OSError, ValueError) as exc:
        # A failed summary degrades to keeping the old history, but programming errors must not be
        # swallowed or the bug hides inside the compaction path.
        logger.warning("compact: 摘要调用失败：%s", exc)
        return None

    text = str(turn.text).strip()
    return text or None


def _summary_message(summary: str, transcript: str) -> str:
    return (
        "[历史摘要] 之前的对话已被压缩，以下是摘要。\n\n"
        f"{summary}\n\n"
        f"（完整记录：{transcript}；需要细节时用 read_file 读回。）"
    )


def tool_result_budget(
    transcript: Transcript,
    *,
    budget: int = TOOL_RESULT_CHAR_BUDGET,
    keep_recent: int = TOOL_RESULT_KEEP_RECENT,
    workdir: Path | None = None,
    tag: str = "",
) -> CompactReport | None:
    """Spill the single largest tool result when the total exceeds the budget, never a recent one."""
    # Only one result is spilled per call, and the budget must exceed the recent set or it never trips.
    before = transcript.tool_chars()
    if before <= budget:
        return None

    indexes = transcript.tool_indexes()
    if len(indexes) <= keep_recent:
        return None

    candidates = [
        (len(transcript.text_at(index)), index)
        for index in indexes[:-keep_recent]
        if not _is_spilled(transcript.text_at(index))
    ]
    if not candidates:
        return None

    size, index = max(candidates)
    if size == 0:
        return None

    path = spill(transcript.text_at(index), "tool-result", workdir, tag)
    if path is None:
        return None

    transcript.set_content(index, spill_notice(path, size, "工具结果"))
    return CompactReport(
        "tool_result_budget",
        f"落盘最大的一项工具结果（保留最近 {keep_recent} 条）",
        before,
        transcript.tool_chars(),
    )


def snip_compact(
    transcript: Transcript,
    *,
    max_messages: int = MAX_MESSAGES,
    keep_head: int = SNIP_KEEP_HEAD,
    keep_tail: int = SNIP_KEEP_TAIL,
) -> CompactReport | None:
    """Drop the middle of a transcript over the message cap, cutting only at safe boundaries."""
    before = len(transcript)
    if before <= max_messages:
        return None

    head_end = min(keep_head, before)
    tail_start = max(head_end, before - keep_tail)

    # Walk each cut to a safe boundary so no tool call is separated from its result.
    while head_end < tail_start and not transcript.is_safe_boundary(head_end):
        head_end += 1
    while tail_start > head_end and not transcript.is_safe_boundary(tail_start):
        tail_start -= 1

    if head_end >= tail_start:
        logger.info("compact: snip_compact 找不到安全切口，跳过本轮")
        return None

    dropped = tail_start - head_end
    marker = {
        "role": "user",
        "content": (
            f"[已裁剪] 为控制上下文长度，中间 {dropped} 条消息被移除"
            "（较早的工具结果如需恢复，见其中的落盘路径）。"
        ),
    }
    transcript.splice(head_end, tail_start, [marker])
    return CompactReport("snip_compact", f"裁掉中间 {dropped} 条", before, len(transcript))


def micro_compact(
    transcript: Transcript,
    *,
    limit: int = CONTEXT_CHAR_LIMIT,
    keep_recent: int = MICRO_COMPACT_KEEP_RECENT,
    target_ratio: float = MICRO_COMPACT_TARGET_RATIO,
    workdir: Path | None = None,
    tag: str = "",
) -> CompactReport | None:
    """Spill older tool results until the context falls under the limit, without calling the model."""
    before = transcript.estimate_chars()
    if before <= limit:
        return None

    target = int(limit * target_ratio)
    indexes = transcript.tool_indexes()
    candidates = indexes[:-keep_recent] if keep_recent else indexes

    spilled = 0
    for index in candidates:
        if transcript.estimate_chars() <= target:
            break

        content = transcript.text_at(index)
        if _is_spilled(content):
            continue

        path = spill(content, "tool-result", workdir, tag)
        if path is None:
            break

        transcript.set_content(index, spill_notice(path, len(content), "工具结果"))
        spilled += 1

    if not spilled:
        return None
    return CompactReport(
        "micro_compact",
        f"落盘 {spilled} 项较早的工具结果（保留最近 {keep_recent} 条）",
        before,
        transcript.estimate_chars(),
    )


def compact_history(
    transcript: Transcript,
    *,
    config: Config,
    chat: Any = chat_completion,
    limit: int = CONTEXT_CHAR_LIMIT,
    workdir: Path | None = None,
    tag: str = "",
) -> CompactReport | None:
    """Save the full transcript, summarize it with one model call, and replace the history."""
    before = transcript.estimate_chars()
    if before <= limit:
        return None

    path = _save_transcript(transcript.as_messages(), workdir, tag)
    summary = _summarize(transcript.as_messages(), config=config, chat=chat)
    if summary is None:
        logger.warning("compact: 摘要生成失败，保留原历史")
        return None

    transcript.replace_all(
        [{"role": "user", "content": _summary_message(summary, path)}]
    )
    return CompactReport(
        "compact_history",
        f"摘要替换历史（完整记录 {path}）",
        before,
        transcript.estimate_chars(),
    )


def reactive_compact(
    transcript: Transcript,
    *,
    config: Config,
    chat: Any = chat_completion,
    keep_recent: int = REACTIVE_KEEP_RECENT,
    workdir: Path | None = None,
    tag: str = "",
) -> CompactReport | None:
    """Last resort once the model reports an overflow: summarize earlier history, keep the tail."""
    before = transcript.estimate_chars()
    messages = transcript.as_messages()

    tail_start = max(0, len(messages) - keep_recent)
    # Move the cut back to a safe boundary so the kept tail never starts mid-exchange.
    while tail_start > 0 and not transcript.is_safe_boundary(tail_start):
        tail_start -= 1

    earlier = messages[:tail_start]
    if not earlier:
        logger.warning("compact: 没有可总结的更早历史，兜底压缩放弃")
        return None

    path = _save_transcript(messages, workdir, tag)
    summary = _summarize(earlier, config=config, chat=chat)
    if summary is None:
        return None

    tail = messages[tail_start:]
    transcript.replace_all(
        [{"role": "user", "content": _summary_message(summary, path)}] + tail
    )
    return CompactReport(
        "reactive_compact",
        f"摘要更早的 {len(earlier)} 条，保留最近 {len(tail)} 条",
        before,
        transcript.estimate_chars(),
    )
