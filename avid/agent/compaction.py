"""压缩子系统：一条 Pi 式压缩通路，阈值或手动触发。

Context 输入 = system prompt + 对话历史 + 用户输入。触发后：保留最近
``keep_recent_turns``（默认 10，约 5–20 之间可配）轮的完整历史，更早的
历史先整段落盘（可回查）再经一次固定 prompt 的 summarize 压成一条摘要
消息。压缩后 context = system prompt + summary + 最近轮历史。

- 切点只在安全边界：assistant 轮与其工具结果同生共死，绝不从批中间切。
- 溢出兜底（PromptTooLong）与主动请求走同一条路径，只是 force=True。
- 游标经 on_compaction 落会话值（诊断 C2）：下一个运行的投影直接从
  摘要形态开始，摘要调用不重花。
- 触发线 = 窗口折算字符 − reserve（给输出留的余量，Pi 同值 16384
  tokens）；无窗口/读数时回落 CONTEXT_CHAR_LIMIT。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..providers.client import LLMError
from ..providers.config import Config
from . import events, prompt
from .transcript import Transcript

logger = logging.getLogger("avid.agent.compaction")

# 无窗口/读数时的回落触发线；阈值常量集中在这里，调参只动这些数字。
CONTEXT_CHAR_LIMIT = 400_000
RESERVE_TOKENS = 16_384
KEEP_RECENT_TURNS = 10

# Spilled files must stay inside the workspace, since the read tool only reads there.
SPILL_DIR = ".avid/context"
# Marker for content that has already been spilled, so it is never treated as raw output again.
SPILL_PREFIX = "[已落盘]"

# 固定的摘要系统提示词：一次性说明保留什么、丢弃什么。
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


@dataclass(frozen=True)
class CompactReport:
    """What one compaction did, logged and counted by the loop."""

    step: str
    detail: str
    before: int
    after: int

    def describe(self) -> str:
        return f"{self.step} — {self.detail}"


@dataclass(frozen=True)
class ContextBudget:
    """压缩预算与组装上限；默认值引用常量，按运行可注入覆盖（单变量对照用）。

    组装上限（bootstrap_chars / skill_always_chars）由 CONTEXT_MAP 的 cap 字段
    以字段名引用，渲染时从这份预算取值——上限可调但不散落。
    """

    bootstrap_chars: int = prompt.AGENTS_MD_MAX_CHARS
    skill_always_chars: int = prompt.SKILL_ALWAYS_TOTAL_MAX_CHARS
    keep_recent_turns: int = KEEP_RECENT_TURNS
    reserve_tokens: int = RESERVE_TOKENS
    context_chars: int = CONTEXT_CHAR_LIMIT
    # Whether the trigger line follows the real window; turn it off when comparing
    # injected values.
    from_window: bool = True


def _next_spill_path(root: Path, kind: str, suffix: str, tag: str = "") -> Path:
    """Return the next spill path, tagged per run when the caller supplies one."""
    global _spill_seq
    with _SPILL_LOCK:  # parallel subagents compact at once, so the sequence must be taken atomically
        _spill_seq += 1
        seq = _spill_seq
    return root / f"{kind}-{tag or _PROCESS_TAG}-{seq:04d}{suffix}"


def _spill_root(root: Path | None = None) -> Path:
    """Return the spill directory under the run workspace root, falling back to the process root."""
    if root is not None:
        return Path(root) / SPILL_DIR
    from .tools import workspace

    return Path(workspace.WORKSPACE_ROOT) / SPILL_DIR


def spill(text: str, kind: str, root: Path | None = None, tag: str = "") -> str | None:
    """Write text under the spill directory and return its workspace-relative path, or None on failure.

    供 large_output_hook 使用：工具结果超长时截断并把全文放到这里，模型用
    read_file 读回。
    """
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


# ---------- 触发预算 ----------


def _chars_per_token(prompt_tokens: int | None, chars: tuple[int, int, int] | None) -> float:
    """实测本会话的字符/token 比；缺失或离谱时回落 2.0（多数中英混合的量级）。"""
    if prompt_tokens is None or prompt_tokens <= 0 or chars is None:
        return 2.0
    total = sum(chars)
    if total <= 0:
        return 2.0
    measured = total / prompt_tokens
    # 离谱的读数不采纳：0.5（token 比字符还多）到 6.0（全 ASCII）之外视为噪声。
    return min(max(measured, 0.5), 6.0)


def trigger_chars(
    *,
    window: int | None,
    prompt_tokens: int | None,
    chars: tuple[int, int, int] | None,
    reserve_tokens: int,
    fallback: int = CONTEXT_CHAR_LIMIT,
) -> int:
    """触发线 = (窗口 − reserve) × 实测字符/token − system 与工具字符。

    reserve 给输出留余量（推理模型输出很长）。窗口或读数缺失时回落 fallback。
    """
    if window is None or window <= 0:
        return fallback
    per_token = _chars_per_token(prompt_tokens, chars)
    budget_chars = int((window - reserve_tokens) * per_token)
    if chars is not None:
        budget_chars -= chars[0] + chars[1]
    return max(1, budget_chars)


def effective_trigger(limits: ContextBudget, state: Any) -> tuple[ContextBudget, int]:
    """Return the trigger line this run uses (window-derived when a reading exists).

    from_window=False 时注入的 context_chars 原样生效——单变量对照的注入不能被派生盖掉。
    """
    if not limits.from_window:
        return limits, limits.context_chars
    trigger = trigger_chars(
        window=state.context_window,
        prompt_tokens=None if state.last_usage is None else state.last_usage.prompt_tokens,
        chars=state.prompt_parts,
        reserve_tokens=limits.reserve_tokens,
        fallback=limits.context_chars,
    )
    return limits, trigger


def announce(report: "CompactReport | None", state: Any) -> None:
    """Record a compaction in one place: one log line, one ledger entry and one event."""
    if report is None:
        return
    logger.info("compact: %s", report.describe())
    # How much a compaction saved is answered by the next model call, not estimated here.
    state.mark_compacted(report.step)
    state.emit(
        events.CONTEXT_COMPACTED,
        step=report.step,
        detail=report.detail,
        before=report.before,
        after=report.after,
    )


# ---------- 压缩通路 ----------


def cut_point(transcript: Transcript, keep_recent_turns: int) -> int:
    """Return the index where the kept window starts: the oldest of the recent turns.

    一轮 = 一条 assistant 消息连同它的全部工具结果；从尾部往前数
    ``keep_recent_turns`` 条 assistant 消息，切点再向安全边界外侧行走——
    绝不从工具批中间切。
    """
    messages = transcript.as_messages()
    rounds = 0
    cut = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            rounds += 1
            if rounds == keep_recent_turns:
                cut = index
                break
    if cut == 0:
        return 0  # 轮数不足一个保留窗口：没有「更早历史」可摘要
    while cut < len(messages) and not transcript.is_safe_boundary(cut):
        cut += 1
    return cut


def run_compaction(
    *,
    transcript: Transcript,
    state: Any,
    config: Config,
    chat: Any,
    limits: ContextBudget,
    force: bool = False,
    on_compaction: Any = None,
) -> CompactReport | None:
    """把保留窗口之外的历史压成一条摘要；返回报告，未触发或失败返回 None。

    force=True（溢出兜底或主动请求）跳过触发线与每运行一次的守护。
    """
    _limits, trigger = effective_trigger(limits, state)
    before = transcript.estimate_chars()
    if not force and before <= trigger:
        return None
    if state.compacted and not force:
        # 自动路径每运行只尝试一次：摘要失败时保留原历史，防逐轮重试风暴。
        logger.info("compact: 本运行已压缩过，跳过")
        return None

    messages = transcript.as_messages()
    cut = cut_point(transcript, limits.keep_recent_turns)
    earlier = messages[:cut]
    if not earlier:
        logger.info("compact: 保留窗口之外没有更早历史，无需压缩")
        return None

    workdir = Path(state.workspace_root) if state.workspace_root else None
    path = _save_transcript(earlier, workdir, state.run_tag)
    summary = _summarize(earlier, config=config, chat=chat)
    if summary is None:
        logger.warning("compact: 摘要生成失败，保留原历史")
        return None

    tail = messages[cut:]
    transcript.replace_all(
        [{"role": "user", "content": _summary_message(summary, path)}] + tail
    )
    state.compacted = True
    report = CompactReport(
        "compact_history",
        f"摘要更早 {len(earlier)} 条，保留最近 {len(tail)} 条（完整记录 {path}）",
        len(messages),
        len(transcript),
    )
    announce(report, state)
    if on_compaction is not None:
        on_compaction(transcript.as_messages()[0], len(tail))
    return report
