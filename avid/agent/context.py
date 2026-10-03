"""Context assembly and compaction: the single place deciding what the model sees each round."""

from __future__ import annotations

import logging
import platform
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from ..providers.config import Config
from ..providers.transcript import Transcript, message_chars
from . import compaction as compact
from . import events, prompt
from .compaction import CompactReport
from .state import RunState

logger = logging.getLogger("avid.agent.context")

#: Placement inside the system prompt, frozen on the first round so the provider prefix cache holds.
SYSTEM = "system"
TAIL = "tail"

#: Built-in block kinds; a new kind needs no change here, since register_source takes any string.
INSTRUCTIONS = "instructions"  # fixed instructions from the caller, or the default text
ENVIRONMENT = "environment"  # working directory plus the available tool names
BOOTSTRAP = "bootstrap"  # the workspace AGENTS.md conventions; a missing file means no block
SKILL_ALWAYS = "skill_always"  # full text of always-marked skills, capped per skill and in total
SKILL_CATALOG = "skill_catalog"  # skill catalog; only load_skill brings the full text in
INJECTED = "injected"  # extra system text injected by a UserPromptSubmit hook
PLAN = "plan"  # the current TODO plan
RUN_STATE = "run_state"  # round, tool-call, denial and compaction counters

# Fixed tail header so the model and log readers can tell this apart from user input.
TAIL_HEADER = "[上下文] 以下是本次请求附带的运行时上下文，不是用户输入。"


@dataclass(frozen=True)
class Block:
    """One piece of context, tagged by kind and placed by section."""

    kind: str
    content: str
    section: str


@dataclass(frozen=True)
class ContextBudget:
    """Compaction thresholds; the defaults reference the constants in policy.compaction."""

    tool_result_chars: int = compact.TOOL_RESULT_CHAR_BUDGET
    tool_result_keep_recent: int = compact.TOOL_RESULT_KEEP_RECENT
    keep_head: int = compact.SNIP_KEEP_HEAD
    keep_tail: int = compact.SNIP_KEEP_TAIL
    context_chars: int = compact.CONTEXT_CHAR_LIMIT
    micro_keep_recent: int = compact.MICRO_COMPACT_KEEP_RECENT
    micro_target_ratio: float = compact.MICRO_COMPACT_TARGET_RATIO
    reactive_keep_recent: int = compact.REACTIVE_KEEP_RECENT
    # Whether the character thresholds follow the real window; turn it off when comparing
    # injected values.
    from_window: bool = True
    # Fraction of the window used as the derived trigger line.
    window_ratio: float = compact.WINDOW_TRIGGER_RATIO


def effective_budget(
    limits: ContextBudget, state: RunState
) -> tuple[ContextBudget, str | None]:
    """Return the thresholds this run uses and their origin, or None when plain defaults apply."""
    if not limits.from_window:
        return limits, None

    # The reading and the part counts come from the same request, so they always arrive as a pair.
    parts = state.prompt_parts
    usage = state.last_usage
    # Chars per token is measured from the last real request, because a fixed ratio is wrong
    # for mixed scripts.
    derived = compact.derived_context_chars(
        window=state.context_window,
        prompt_tokens=None if usage is None else usage.prompt_tokens,
        chars=parts,
        ratio=limits.window_ratio,
    )
    # A missing reading or part count falls back verbatim to the constant, so the default
    # path stays unchanged.
    if derived is None or parts is None:
        return limits, None

    limit, per_token = derived
    note = (
        f"阈值随窗口派生：{limit} 字符"
        f"（窗口 {state.context_window} × {limits.window_ratio:g} × "
        f"实测 {per_token:.2f} 字符/token − 系统与工具 {parts[0] + parts[1]} 字符）"
    )
    return replace(limits, context_chars=limit), note


def announce(report: CompactReport | None, state: RunState) -> None:
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


@dataclass(frozen=True)
class ComposedRequest:
    """One request's final shape: the system prompt, messages and per-block parts."""

    system: str
    messages: list[dict[str, Any]]
    parts: dict[str, int]
    reports: list[CompactReport]

    @property
    def changed(self) -> bool:
        """Whether this composition performed any compaction."""
        return bool(self.reports)

    @property
    def system_chars(self) -> int:
        return len(self.system)

    @property
    def messages_chars(self) -> int:
        """Characters of history plus the tail, under the key the loop reports to the ledger."""
        return self.parts.get("messages", 0)


class ContextManager:
    """Per-run context assembly and budget, constructed once and called every round."""

    def __init__(
        self,
        *,
        transcript: Transcript,
        state: RunState,
        config: Config,
        instructions: str | None = None,
        tool_names: Iterable[str] = (),
        budget: ContextBudget | None = None,
        summarize: Any = None,
        on_compaction: Callable[[dict[str, Any], int], None] | None = None,
    ) -> None:
        self.transcript = transcript
        self.state = state
        self.config = config
        # The caller's instruction override, as benchmarks and subagents each carry one;
        # None falls back to the default text from policy.prompt.
        self.instructions = (
            instructions if instructions is not None else prompt.DEFAULT_INSTRUCTIONS
        )
        self.tool_names = list(tool_names)
        self.budget = budget or ContextBudget()
        self.summarize = summarize
        # ④/⑤ 替换历史后回调（summary 消息, keep 条数）：调用方借此把游标落盘，
        # 让下一次运行的投影直接从摘要形态开始（诊断 C2）。
        self.on_compaction = on_compaction
        self._sources: dict[str, Callable[[], Block | None]] = {}
        # The system prompt is frozen on the first compose so the provider prefix cache stays valid.
        self._system: str | None = None
        self._system_parts: dict[str, int] = {}
        self._register_defaults()

    def register_source(self, kind: str, source: Callable[[], Block | None]) -> None:
        """Register or replace a block source; a SYSTEM block only binds on the first compose."""
        self._sources[kind] = source

    def _register_defaults(self) -> None:
        self.register_source(
            INSTRUCTIONS, lambda: Block(INSTRUCTIONS, self.instructions, SYSTEM)
        )
        self.register_source(
            ENVIRONMENT, lambda: Block(ENVIRONMENT, self._render_environment(), SYSTEM)
        )
        self.register_source(BOOTSTRAP, self._bootstrap_block)
        self.register_source(SKILL_ALWAYS, self._always_skills_block)
        self.register_source(
            SKILL_CATALOG, lambda: Block(SKILL_CATALOG, self._render_skills(), SYSTEM)
        )
        self.register_source(PLAN, self._plan_block)
        self.register_source(RUN_STATE, self._run_state_block)

    def _render_environment(self) -> str:
        # Deferred import: the tools package pulls in policy.skills, so a top-level
        # import would be circular.
        from .tools import workspace

        # The run's workspace root wins here, so one process can serve several workspaces.
        root = self.state.workspace_root or workspace.WORKSPACE_ROOT
        names = "、".join(self.tool_names) if self.tool_names else "（无）"
        runtime = (
            f"{platform.system()} {platform.machine()} / Python {platform.python_version()}"
        )
        return (
            "## 环境\n"
            f"工作目录：{root}\n"
            f"运行时：{runtime}；今天：{date.today().isoformat()}\n"
            f"可用工具：{names}\n"
            "\n"
            "Act, don't explain."
        )

    def _render_skills(self) -> str:
        catalog = self.state.skills.catalog()
        return (
            "## 可用技能\n"
            f"{catalog or '（当前没有可用技能）'}\n\n"
            "Use load_skill to read the full instructions when a skill applies."
        )

    def _bootstrap_block(self) -> Block | None:
        # Read once per run: SYSTEM sources are collected only on the first compose,
        # which is also what keeps the frozen prefix byte-stable afterwards.
        root = self.state.workspace_root
        if not root:
            return None
        try:
            text = (Path(root) / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.strip():
            return None
        if len(text) > prompt.AGENTS_MD_MAX_CHARS:
            text = text[: prompt.AGENTS_MD_MAX_CHARS] + prompt.TRUNCATION_NOTE
        return Block(BOOTSTRAP, f"## 工作区约定\n{text}", SYSTEM)

    def _always_skills_block(self) -> Block | None:
        # Capping lives here, not in SkillLoader: how much may stay resident is a
        # prompt-budget decision. The total cap skips by name order instead of
        # aborting, so one oversized skill does not evict the smaller ones.
        total = 0
        sections: list[str] = []
        for name, body in self.state.skills.always_bodies():
            if len(body) > prompt.SKILL_ALWAYS_MAX_CHARS:
                body = body[: prompt.SKILL_ALWAYS_MAX_CHARS] + prompt.TRUNCATION_NOTE
            if total + len(body) > prompt.SKILL_ALWAYS_TOTAL_MAX_CHARS:
                logger.warning("常驻技能总量超上限，跳过：%s", name)
                continue
            total += len(body)
            sections.append(f"### {name}\n{body}")
        if not sections:
            return None
        return Block(
            SKILL_ALWAYS,
            "## 常驻技能\n以下技能已全文载入，无需再调用 load_skill：\n\n"
            + "\n\n".join(sections),
            SYSTEM,
        )

    def _plan_block(self) -> Block | None:
        # An empty plan renders a placeholder, so no plan at all means no block.
        if not self.state.todo.items:
            return None
        return Block(PLAN, f"## 当前计划\n{self.state.todo.render()}", TAIL)

    def _run_state_block(self) -> Block:
        state = self.state
        return Block(
            RUN_STATE,
            "## 运行状态\n"
            f"第 {state.round} 轮；已执行 {state.tool_calls} 次工具调用；"
            f"被拒 {state.denials} 次（当前连击 {state.denial_streak}）；"
            f"压缩 {state.compactions} 次。",
            TAIL,
        )

    def _collect(self, section: str) -> list[Block]:
        # Sources are keyed by kind, so re-registering a kind replaces its previous source.
        blocks: list[Block] = []
        for source in self._sources.values():
            block = source()
            if block is not None and block.section == section and block.content:
                blocks.append(block)
        return blocks

    def compose(self, *, injected: list[str] | None = None) -> ComposedRequest:
        """Compact and render; injected text joins the system prompt on the first round only."""
        reports = self._compact()
        if self._system is None:
            # Injected background that later disappears is worse than never injecting it at all.
            self._freeze_system(list(injected or []))
        return self._assemble(reports)

    def system_prompt(self) -> str:
        """Render the system prompt alone, without compaction or tail, for baseline arms."""
        if self._system is None:
            self._freeze_system([])
        assert self._system is not None  # _freeze_system always assigns
        return self._system

    def _freeze_system(self, injected: list[str]) -> None:
        # Per-kind character counts are frozen with the text so accounting matches the prompt.
        blocks = self._collect(SYSTEM)
        texts = [block.content for block in blocks]
        texts += [str(text) for text in injected if str(text).strip()]
        self._system = "\n\n".join(texts)
        self._system_parts = {}
        for block in blocks:
            self._system_parts[block.kind] = (
                self._system_parts.get(block.kind, 0) + len(block.content)
            )
        for text in injected:
            self._system_parts[INJECTED] = (
                self._system_parts.get(INJECTED, 0) + len(str(text))
            )

    def render(self) -> ComposedRequest:
        """Re-render after a fallback compaction; the system prompt itself stays fixed."""
        if self._system is None:
            raise RuntimeError("render() 之前必须先 compose()")
        return self._assemble([])

    def _assemble(self, reports: list[CompactReport]) -> ComposedRequest:
        # The tail becomes one extra user message and is never written to the transcript.
        tail_blocks = self._collect(TAIL)
        messages = self.transcript.as_messages()
        tail_chars = 0
        if tail_blocks:
            content = TAIL_HEADER + "\n\n" + "\n\n".join(
                block.content for block in tail_blocks
            )
            message = {"role": "user", "content": content}
            messages.append(message)
            tail_chars = message_chars(message)

        parts = dict(self._system_parts)
        for block in tail_blocks:
            parts[block.kind] = parts.get(block.kind, 0) + len(block.content)
        parts["history"] = self.transcript.estimate_chars()
        parts["messages"] = parts["history"] + tail_chars
        return ComposedRequest(
            system=self._system or "",
            messages=messages,
            parts=parts,
            reports=reports,
        )

    def _compact(self) -> list[CompactReport]:
        """Run the pipeline cheapest first; the fourth step happens at most once per run."""
        limits, note = effective_budget(self.budget, self.state)
        if note is not None:
            logger.debug("compact: %s", note)
        reports: list[CompactReport] = []
        # Spill files follow the workspace root, because one process can serve several workspaces.
        workdir = Path(self.state.workspace_root) if self.state.workspace_root else None

        def run(report: CompactReport | None) -> None:
            if report is None:
                return
            if note is not None:
                # A derived threshold records its origin, which the UI reads to explain it.
                report = replace(report, detail=f"{report.detail}（{note}）")
            reports.append(report)
            announce(report, self.state)

        # Steps one and two need no API call, so they run every round.
        run(
            compact.tool_result_budget(
                self.transcript,
                budget=limits.tool_result_chars,
                keep_recent=limits.tool_result_keep_recent,
                workdir=workdir,
                tag=self.state.run_tag,
            )
        )
        run(
            compact.snip_compact(
                self.transcript,
                # ② 与 ③④ 共用派生预算：条数不再单独触发（诊断 C1）
                max_chars=limits.context_chars,
                keep_head=limits.keep_head,
                keep_tail=limits.keep_tail,
            )
        )

        # Step three is free, so it comes before the paid one.
        if self.transcript.estimate_chars() > limits.context_chars:
            run(
                compact.micro_compact(
                    self.transcript,
                    limit=limits.context_chars,
                    keep_recent=limits.micro_keep_recent,
                    target_ratio=limits.micro_target_ratio,
                    workdir=workdir,
                    tag=self.state.run_tag,
                )
            )

        # Step four pays for a summarization call, so state allows it at most once per run.
        if self.transcript.estimate_chars() > limits.context_chars:
            if self.state.compacted:
                logger.info("compact: 自动压缩本运行已用过一次，跳过")
            else:
                report = compact.compact_history(
                    self.transcript,
                    config=self.config,
                    chat=self.summarize,
                    limit=limits.context_chars,
                    workdir=workdir,
                    tag=self.state.run_tag,
                )
                if report is not None and self.on_compaction is not None:
                    self.on_compaction(self.transcript.as_messages()[0], 0)
                if report is not None:
                    # Only this branch sets the flag, which is what bounds step four to one run.
                    self.state.compacted = True
                run(report)

        return reports

    def reactive(self) -> CompactReport | None:
        """Fallback after a provider overflow: summarize older history and keep a recent tail."""
        report = compact.reactive_compact(
            self.transcript,
            config=self.config,
            chat=self.summarize,
            workdir=Path(self.state.workspace_root)
            if self.state.workspace_root
            else None,
            keep_recent=self.budget.reactive_keep_recent,
            tag=self.state.run_tag,
        )
        # The caller bounds this to one attempt per run through state.retried.
        announce(report, self.state)
        if report is not None and self.on_compaction is not None:
            self.on_compaction(
                self.transcript.as_messages()[0], compact.REACTIVE_KEEP_RECENT
            )
        return report
