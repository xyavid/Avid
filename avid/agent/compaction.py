"""Compaction subsystem: one path that keeps the recent turns and folds everything earlier into a
single checkpoint message, triggered by the token budget or forced.

The cut point never lands inside a tool batch, and the trigger line is derived from the model
window minus the output reserve, falling back to CONTEXT_CHAR_LIMIT when no window reading exists.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..providers.client import LLMError
from ..providers.config import Config
from . import events, prompt
from .transcript import Transcript, text_of

logger = logging.getLogger("avid.agent.compaction")

# Fallback trigger line when no window reading exists; the tunable thresholds all live here.
CONTEXT_CHAR_LIMIT = 400_000
RESERVE_TOKENS = 16_384
KEEP_RECENT_TURNS = 10

# Spilled files must stay inside the workspace, since the read tool only reads there.
SPILL_DIR = ".avid/context"
# Marker for content that has already been spilled, so it is never treated as raw output again.
SPILL_PREFIX = "[已落盘]"

# System prompt of the checkpoint engine, shared by create and update: the summary is model-written
# but is the original text, so the engine must not solve the task, chat on or call tools.
COMPACTION_SYSTEM = (
    "You are a context-compaction engine for a coding agent.\n"
    "Your task is to transform the provided conversation state into a compact, faithful "
    "checkpoint that another agent can use to continue the task.\n"
    "\n"
    "Do not solve the task.\n"
    "Do not continue the conversation.\n"
    "Do not call tools.\n"
    "Only produce the requested checkpoint.\n"
    "\n"
    "Write the checkpoint body in the language the conversation uses; keep the section "
    "headings exactly as given. Never invent missing facts."
)

#: Shared checkpoint skeleton; it is all the next agent receives, so both prompts keep it identical.
CHECKPOINT_STRUCTURE = (
    "## Goal\n"
    "## Constraints & Preferences\n"
    "## Findings\n"
    "### Verified\n"
    "### Hypotheses\n"
    "## Progress\n"
    "### Done\n"
    "### In Progress\n"
    "### Blocked\n"
    "## Key Decisions\n"
    "## Next Steps\n"
    "## Critical Context"
)

#: Shared section guide: how each section is filled must match, or create and update cannot compare.
STRUCTURE_GUIDE = (
    "Section notes:\n"
    "- Progress items carry - [x] when done and - [ ] when not; ### Blocked holds what is "
    "stuck and why.\n"
    "- Errors that were hit, and how they were resolved, go under ## Critical Context with the "
    "exact command or message.\n"
    "- Keep exact file paths, function names, commands and identifiers everywhere."
)

#: First compaction task block: keep/drop lists, the verified-vs-hypothesis split, the structure.
CREATE_TASK = (
    "Create a continuation checkpoint.\n"
    "\n"
    "Preserve:\n"
    "- user intent\n"
    "- explicit constraints and stated preferences\n"
    "- important decisions\n"
    "- verified facts\n"
    "- current implementation state\n"
    "- errors and resolutions\n"
    "- exact file paths, function names, commands and identifiers\n"
    "- pending work\n"
    "- next action\n"
    "\n"
    "Remove:\n"
    "- obsolete hypotheses\n"
    "- redundant explanation\n"
    "- transient narration\n"
    "- duplicated tool output\n"
    "\n"
    "Never invent missing facts.\n"
    "\n"
    "Never merge the two finding lists:\n"
    "- ### Verified: only what a tool result or an explicit user statement in the material "
    "backs up. Keep the evidence short (file, command, output).\n"
    "- ### Hypotheses: everything inferred, suspected or still unverified. Tag each entry "
    "[hypothesis]; one the material ruled out stays here tagged [rejected], with what ruled "
    "it out. Nothing moves up to Verified without evidence.\n"
    "\n"
    f"{STRUCTURE_GUIDE}\n"
    "\n"
    "Use this exact structure:\n"
    f"{CHECKPOINT_STRUCTURE}"
)

#: Update task block: revise the old checkpoint with new material instead of re-summarizing.
UPDATE_TASK = (
    "Update the existing structured summary with new information.\n"
    "\n"
    "Rules:\n"
    "- PRESERVE all existing information from the previous summary, wording included.\n"
    "- ADD new progress, decisions and context from the new material.\n"
    "- UPDATE Progress: move items from ### In Progress to ### Done when they are completed.\n"
    "- UPDATE ### Next Steps based on what was accomplished.\n"
    "- PRESERVE exact file paths, function names and error messages.\n"
    "- New evidence wins: when the new material contradicts an entry, correct that entry in "
    "place instead of appending a second version of it.\n"
    "- Refresh paths and status against the repo state and the new material: what no longer "
    "exists, or is already done, does not survive. If something is no longer relevant, you "
    "may remove it.\n"
    "- Keep the finding lists honest: a hypothesis the material confirmed moves to "
    "### Verified with its evidence; a ruled-out one stays under ### Hypotheses tagged "
    "[rejected] with the reason. Never promote a hypothesis without evidence.\n"
    "- Never invent missing facts.\n"
    "\n"
    f"{STRUCTURE_GUIDE}\n"
    "\n"
    "Use this exact format:\n"
    f"{CHECKPOINT_STRUCTURE}"
)

#: Head and footer marks of a summary message; checkpoint_of reads the checkpoint body back out.
SUMMARY_MESSAGE_HEADER = "[历史摘要] 之前的对话已被压缩，以下是摘要。"
_SUMMARY_FOOTER_MARK = "（完整记录："

# Repo-state probe, one input of the update path; best effort only, so a timeout, error or non-repo
# result reads as absent and never fails the compaction.
REPO_STATE_TIMEOUT_SECONDS = 2.0
REPO_STATE_MAX_CHARS = 2_000
REPO_STATE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("rev-parse", "--abbrev-ref", "HEAD"),
    ("log", "-1", "--oneline"),
    ("status", "--porcelain", "-uno"),
    ("diff", "--stat"),
)

# A per-process tag keeps spill filenames unique across restarts, so a summary's read-back path
# stays valid instead of being silently overwritten by the next run.
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
    """Compaction budget and assembly caps; a run may inject overrides, and CONTEXT_MAP references
    the assembly caps by field name so the limits stay adjustable in one place."""

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
    with _SPILL_LOCK:  # parallel subagents compact at once, so the counter is taken atomically
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
    """Write text under the spill directory and return its workspace-relative path, or None; the
    large-output hook spills oversized tool output here, read back with read_file."""
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
    """Write the full transcript as JSON and return its path, or a placeholder on failure."""
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


def _tag(name: str, body: str) -> str:
    """Wraps one input of the summarization request; the tags are the prompt's table of contents."""
    return f"<{name}>\n{body}\n</{name}>"


#: Substitute text written into <conversation> when the update path has no new material.
_NO_NEW_MATERIAL = "(no new conversation since the checkpoint)"


def render_conversation(messages: list[dict[str, Any]]) -> str:
    """Render a slice of history as role-labelled text, the single place message shapes flatten;
    tool arguments stay raw because the next agent needs the exact command or path."""
    names = {
        str(call.get("id")): str((call.get("function") or {}).get("name", "?"))
        for message in messages
        for call in message.get("tool_calls") or []
    }
    blocks: list[str] = []
    for message in messages:
        role = str(message.get("role", "?"))
        text = text_of(message.get("content")).strip()
        if role == "tool":
            # A bare call_id does not say whose result this is, so tag the tool name.
            name = names.get(str(message.get("tool_call_id")), "unknown")
            head = f"[tool result: {name}]"
        else:
            head = f"[{role}]"
        block = f"{head}\n{text}" if text else head
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            block += f"\n[tool call] {function.get('name', '?')}({function.get('arguments', '')})"
        blocks.append(block)
    return "\n\n".join(blocks)


def _original_request(messages: list[dict[str, Any]]) -> str:
    """The request that opened the material being compacted; when it is not there, say so."""
    for message in messages:
        if message.get("role") != "user":
            continue
        text = text_of(message.get("content")).strip()
        if text and checkpoint_of(text) is None:
            return text
    return "(the original request is not part of this material)"


def _create_message(original_request: str, conversation: str) -> str:
    return "\n\n".join(
        [
            _tag("original-request", original_request),
            _tag("conversation", conversation),
            _tag("compaction-task", CREATE_TASK),
        ]
    )


def _update_message(checkpoint: str, conversation: str, repo: str | None) -> str:
    blocks = [
        _tag("previous-checkpoint", checkpoint),
        # Two compactions in a row (or a compaction right before an overflow) leave no new material:
        # say so, rather than handing the model an empty block to guess about.
        _tag("conversation", conversation or _NO_NEW_MATERIAL),
    ]
    if repo:
        blocks.append(_tag("repo-state", repo))
    blocks.append(_tag("update-task", UPDATE_TASK))
    return "\n\n".join(blocks)


def checkpoint_of(content: str) -> str | None:
    """Reads the checkpoint back out of a summary message; None when it is an ordinary message."""
    if not content.startswith(SUMMARY_MESSAGE_HEADER):
        return None
    body = content[len(SUMMARY_MESSAGE_HEADER) :].strip()
    footer = body.rfind(_SUMMARY_FOOTER_MARK)
    if footer != -1:
        body = body[:footer].strip()
    return body or None


def previous_checkpoint(messages: list[dict[str, Any]]) -> str | None:
    """The checkpoint the transcript opens with, if any, which is also the first-vs-update boundary
    and therefore needs no separate cursor."""
    if not messages or messages[0].get("role") != "user":
        return None
    return checkpoint_of(text_of(messages[0].get("content")))


def _git(root: Path, *args: str) -> str | None:
    """One read-only git query; None when git is missing, slow or unhappy."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=REPO_STATE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def repo_state(root: str | None) -> str | None:
    """Summarize the working tree for the update prompt, best effort only: a missing git, a timeout
    or a failed command reads as no block, since compaction must never fail with the probe."""
    if not root:
        return None
    path = Path(root)
    blocks: list[str] = []
    for args in REPO_STATE_COMMANDS:
        out = _git(path, *args)
        if out is None:
            continue
        blocks.append(f"$ git {' '.join(args)}\n{out or '(none)'}")
    if not blocks:
        return None
    return "\n\n".join(blocks)[:REPO_STATE_MAX_CHARS]


def _summarize(body: str, *, config: Config, chat: Any, system: str) -> str | None:
    request = [{"role": "user", "content": body}]
    try:
        # No max_tokens is sent: reasoning tokens share that budget and a hard cap yields an empty
        # summary, which would silently turn compaction into keeping the old history.
        turn = chat(config, request, system=system)
    except (LLMError, OSError, ValueError) as exc:
        # A failed summary degrades to keeping the old history, but programming errors must not be
        # swallowed or the bug hides inside the compaction path.
        logger.warning("compact: 摘要调用失败：%s", exc)
        return None

    text = str(turn.text).strip()
    return text or None


def _summary_message(summary: str, transcript: str) -> str:
    return (
        f"{SUMMARY_MESSAGE_HEADER}\n\n"
        f"{summary}\n\n"
        f"{_SUMMARY_FOOTER_MARK}{transcript}；需要细节时用 read_file 读回。）"
    )


# ---------- trigger budget ----------


def _chars_per_token(prompt_tokens: int | None, chars: tuple[int, int, int] | None) -> float:
    """Measured chars/token for this session; missing or implausible readings fall back to 2.0."""
    if prompt_tokens is None or prompt_tokens <= 0 or chars is None:
        return 2.0
    total = sum(chars)
    if total <= 0:
        return 2.0
    measured = total / prompt_tokens
    # Implausible readings are noise: anything outside 0.5..6.0 is rejected.
    return min(max(measured, 0.5), 6.0)


def trigger_chars(
    *,
    window: int | None,
    prompt_tokens: int | None,
    chars: tuple[int, int, int] | None,
    reserve_tokens: int,
    fallback: int = CONTEXT_CHAR_LIMIT,
) -> int:
    """Trigger line = (window − reserve) × measured chars/token − system and tools characters, where
    the reserve leaves room for output (reasoning models answer at length)."""
    if window is None or window <= 0:
        return fallback
    per_token = _chars_per_token(prompt_tokens, chars)
    budget_chars = int((window - reserve_tokens) * per_token)
    if chars is not None:
        budget_chars -= chars[0] + chars[1]
    return max(1, budget_chars)


def effective_trigger(limits: ContextBudget, state: Any) -> tuple[ContextBudget, int]:
    """Return the trigger line this run uses, derived from the window when a reading exists, unless
    from_window=False keeps the injected context_chars exactly for a controlled arm."""
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


# ---------- compaction path ----------


def cut_point(transcript: Transcript, keep_recent_turns: int) -> int:
    """Return the index where the kept window starts, the oldest of the recent turns; a round is one
    assistant message with all its tool results, so the cut never splits a tool batch."""
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
        return 0  # fewer rounds than the keep window: no earlier history exists to summarize
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
    """Fold everything outside the keep window into one summary message, force=True skipping the
    trigger line and the once-per-run guard; None means nothing ran, keeping the old history."""
    _limits, trigger = effective_trigger(limits, state)
    before = transcript.estimate_chars()
    if not force and before <= trigger:
        return None
    if state.compacted and not force:
        # The automatic path tries once per run: a failed summary keeps the old history, so a
        # per-round retry storm cannot start.
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
    checkpoint = previous_checkpoint(messages)
    if checkpoint is None:
        body = _create_message(_original_request(earlier), render_conversation(earlier))
        mode = "新建检查点"
    else:
        # The previous checkpoint is the first message of this history, so the new material starts
        # after it and nothing is fed twice; repo_state is probed only here, never on a first pass.
        body = _update_message(
            checkpoint,
            render_conversation(earlier[1:]),
            repo_state(state.workspace_root),
        )
        mode = "更新检查点"
    # One engine prompt only: the task block says create or update, so the system prompt never
    # follows the mode.
    summary = _summarize(body, config=config, chat=chat, system=COMPACTION_SYSTEM)
    if summary is None:
        logger.warning("compact: 检查点生成失败，保留原历史")
        return None

    tail = messages[cut:]
    transcript.replace_all(
        [{"role": "user", "content": _summary_message(summary, path)}] + tail
    )
    state.compacted = True
    report = CompactReport(
        "compact_history",
        f"{mode}：压缩更早 {len(earlier)} 条，保留最近 {len(tail)} 条（完整记录 {path}）",
        len(messages),
        len(transcript),
    )
    announce(report, state)
    if on_compaction is not None:
        on_compaction(transcript.as_messages()[0], len(tail))
    return report
