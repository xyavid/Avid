"""压缩子系统：一条 Pi 式压缩通路，阈值或手动触发。

Context 输入 = system prompt + 对话历史 + 用户输入。触发后：保留最近
``keep_recent_turns``（默认 10，约 5–20 之间可配）轮的完整历史，更早的
历史先整段落盘（可回查）再经一次 summarize 压成一条检查点消息。压缩后
context = system prompt + 检查点 + 最近轮历史。

检查点走两份 prompt（``CREATE_TASK`` 新建 / ``UPDATE_TASK`` 更新，共用
``COMPACTION_SYSTEM`` 与 ``CHECKPOINT_STRUCTURE``）。分界是 transcript 头部：
还是普通消息就是首次压缩，带上最初请求与被压缩的历史；已经是检查点就走更新路径——
上一份检查点 + 新素材 + 仓库现状（``repo_state`` 探针），按现状改而不是从头摘要一遍。
两份都要求把事实与假设分开（``### Verified`` / ``### Hypotheses``）：假设被推翻也不删，
标 ``[rejected]`` 留痕，否则下一轮会有人把假设当事实用。

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

# 无窗口/读数时的回落触发线；阈值常量集中在这里，调参只动这些数字。
CONTEXT_CHAR_LIMIT = 400_000
RESERVE_TOKENS = 16_384
KEEP_RECENT_TURNS = 10

# Spilled files must stay inside the workspace, since the read tool only reads there.
SPILL_DIR = ".avid/context"
# Marker for content that has already been spilled, so it is never treated as raw output again.
SPILL_PREFIX = "[已落盘]"

# 检查点引擎的系统提示：创建与更新共用（身份、禁止事项、语言）。摘要是模型写的，但它是
# 原件——不许解题、不许接着聊、不许调工具，只出一份下一个 agent 能直接续上的检查点。
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

#: 检查点结构：两份 prompt 共用一份骨架——结构漂了，下一个 agent 就找不着东西，
#: 而它只拿到这一份文本。
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

#: 两份任务块共用的分段说明：光给段名不够，填法也要一致，否则创建与更新的产物没法比。
STRUCTURE_GUIDE = (
    "Section notes:\n"
    "- Progress items carry - [x] when done and - [ ] when not; ### Blocked holds what is "
    "stuck and why.\n"
    "- Errors that were hit, and how they were resolved, go under ## Critical Context with the "
    "exact command or message.\n"
    "- Keep exact file paths, function names, commands and identifiers everywhere."
)

#: 首次压缩的任务块：保留/删去清单 + 事实与假设的分层 + 输出结构。
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

#: 更新路径的任务块：以旧检查点为底座，用新素材改它，而不是重新摘要一遍。
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

#: 摘要消息的头与脚注标记：``checkpoint_of`` 靠它把检查点正文取回来（更新路径的输入）。
SUMMARY_MESSAGE_HEADER = "[历史摘要] 之前的对话已被压缩，以下是摘要。"
_SUMMARY_FOOTER_MARK = "（完整记录："

# 仓库现状探针：更新路径的输入之一。只做锦上添花——超时、报错、不是仓库都当作没有。
REPO_STATE_TIMEOUT_SECONDS = 2.0
REPO_STATE_MAX_CHARS = 2_000
REPO_STATE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("rev-parse", "--abbrev-ref", "HEAD"),
    ("log", "-1", "--oneline"),
    ("status", "--porcelain", "-uno"),
    ("diff", "--stat"),
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


def _tag(name: str, body: str) -> str:
    """Wraps one input of the summarization request; the tags are the prompt's table of contents."""
    return f"<{name}>\n{body}\n</{name}>"


#: 更新路径遇到空素材时写进 <conversation> 的替代文本。
_NO_NEW_MATERIAL = "(no new conversation since the checkpoint)"


def render_conversation(messages: list[dict[str, Any]]) -> str:
    """Renders a slice of history as role-labelled text.

    The request carries one user message (see COMPACTION_SYSTEM), so this is the single place
    where message shapes are flattened. Tool calls keep their raw arguments on purpose: an
    exact command or path is what the next agent needs, and it cannot re-derive it.
    """
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
            # 光有 call_id 读不出这是谁的结果，标上工具名
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
        # 连着压两次（或刚压完就溢出兜底）时新素材是空的：明说，别给一个空块让模型自己猜。
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
    """The checkpoint the transcript opens with, if any.

    摘要消息在不在头部，就是「首次压缩」与「更新压缩」的分界——它也正好是「上一份检查点」
    的存放处，所以不需要另存一份游标。
    """
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
    """Summarizes the working tree for the update prompt: branch, HEAD, tracked changes, diff size.

    只做锦上添花：不是 git 仓库、没装 git、超时或命令失败，一律当作没有这段——压缩不能
    因为探针失败而失败。代价是更新时多几条只读 git 进程；首次压缩不探（那时候没有可更新
    的检查点，探了也没人用）。
    """
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
    checkpoint = previous_checkpoint(messages)
    if checkpoint is None:
        body = _create_message(_original_request(earlier), render_conversation(earlier))
        mode = "新建检查点"
    else:
        # 上一份检查点就是这段历史的第一条消息：它已经吃掉了那部分内容，新素材从它之后算起，
        # 同一段内容不喂两遍。repo_state 只在这条路径上探——首次压缩没有可更新的东西。
        body = _update_message(
            checkpoint,
            render_conversation(earlier[1:]),
            repo_state(state.workspace_root),
        )
        mode = "更新检查点"
    # 引擎提示只有一份：是新建还是更新由任务块说，系统提示不跟着模式漂。
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
