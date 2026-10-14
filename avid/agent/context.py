"""Context assembly: what the model sees each round, from the CONTEXT_MAP declaration table plus the
engine that renders it.

Frozen blocks (instructions, environment, workspace AGENTS.md, skills) are joined once and stay
byte-stable to keep the provider prefix cache, which the compaction call then reuses; the per-round
blocks (injected text, plan, run state) join a tail note rebuilt every round and never written to
the transcript. Nothing an extension injects may reach the frozen blocks, or the prefix would break
under it.
"""

from __future__ import annotations

import json
import logging
import platform
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from ..providers.client import PromptTooLongError, Turn
from ..providers.config import Config
from . import compaction as compact
from . import prompt
from .compaction import CompactReport, ContextBudget
from .state import RunState
from .transcript import Transcript, message_chars

logger = logging.getLogger("avid.agent.context")

#: Block destination: system joins the frozen prefix, tail joins the per-round note.
SYSTEM = "system"
TAIL = "tail"

#: Block lifecycle: frozen is fixed by the first compose, per_round is collected every round.
FROZEN = "frozen"
PER_ROUND = "per_round"

#: Built-in block kinds; register_block / register_source extend the table, so this list is closed.
INSTRUCTIONS = "instructions"
ENVIRONMENT = "environment"
BOOTSTRAP = "bootstrap"
SKILL_ALWAYS = "skill_always"
SKILL_CATALOG = "skill_catalog"
INJECTED = "injected"  # text injected by a UserPromptSubmit hook, rendered in the tail
PLAN = "plan"
RUN_STATE = "run_state"

# Fixed tail header, so model and logs can both tell this is not user input.
TAIL_HEADER = "[上下文] 以下是本次请求附带的运行时上下文，不是用户输入。"


@dataclass(frozen=True)
class BlockSpec:
    """Full declaration of one context block: section, stability, cap and text source, where None
    from the source leaves the block out and a None title renders the body without a heading."""

    kind: str
    title: str | None
    section: str
    stability: str
    # None = no cap; int = body characters; str = a ContextBudget field name resolved per run.
    # The cap constrains the body, headings excluded.
    cap: "int | str | None"
    source: Callable[["ContextManager"], str | None]

    def render(self, body: str) -> str:
        if self.title is None:
            return body
        return f"## {self.title}\n{body}"


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
    """Assembly engine for every round; blocks come from CONTEXT_MAP, compaction is not here."""

    def __init__(
        self,
        *,
        transcript: Transcript,
        state: RunState,
        config: Config,
        instructions: str | None = None,
        tool_names: Iterable[str] = (),
        tool_schemas: list[dict[str, Any]] | None = None,
        budget: ContextBudget | None = None,
        summarize: Any = None,
        chat: Callable[..., Turn] | None = None,
        max_tokens: int | None = None,
        on_compaction: Callable[[dict[str, Any], int], None] | None = None,
    ) -> None:
        self.transcript = transcript
        self.state = state
        self.config = config
        # Caller-supplied instructions (a subagent or a test arm); None falls back to the default.
        self.instructions = (
            instructions if instructions is not None else prompt.DEFAULT_INSTRUCTIONS
        )
        self.tool_names = list(tool_names)
        # The full schemas, not just the names: the compaction call sends them to keep the wire
        # prefix identical to this round's request.
        self.tool_schemas = list(tool_schemas or [])
        self.budget = budget or ContextBudget()
        self.summarize = summarize
        # The round's own call, as opposed to summarize: call() sends with these two.
        self.chat = chat
        self.max_tokens = max_tokens
        # Callback after the history is replaced (summary message, kept tail size); without it the
        # compaction stays in memory only.
        self.on_compaction = on_compaction
        self._specs: dict[str, BlockSpec] = {spec.kind: spec for spec in CONTEXT_MAP}
        self._extra: list[BlockSpec] = []
        # The system prompt is frozen on the first compose so the provider prefix cache stays valid.
        self._system: str | None = None
        self._system_parts: dict[str, int] = {}
        # Injected text is re-rendered into the tail every round, never frozen into the prefix.
        self._injected: list[str] = []

    @classmethod
    def from_spec(
        cls,
        spec: Any,
        *,
        transcript: Transcript,
        state: RunState,
        on_compaction: Callable[[dict[str, Any], int], None] | None = None,
    ) -> "ContextManager":
        """Assemble from a RunSpec — the one construction a round and /compact must share, because a
        compaction only hits the cache when its prefix comes out identical to the round's."""
        return cls(
            transcript=transcript,
            state=state,
            config=spec.config,
            instructions=spec.instructions,
            tool_names=spec.tool_names,
            tool_schemas=spec.tools,
            budget=spec.budget,
            summarize=spec.summarize or spec.chat,
            chat=spec.chat,
            max_tokens=spec.max_tokens,
            on_compaction=on_compaction,
        )

    # ---- extension points ----

    def register_block(self, spec: BlockSpec) -> None:
        """Append a custom block declaration."""
        self._extra.append(spec)

    def register_source(self, kind: str, source: Callable[[], str | None]) -> None:
        """Replace the text source of one kind, or append an unknown kind as a frozen system block;
        a convenience source takes no arguments, unlike one declared in the table."""
        if kind in self._specs:
            self._specs[kind] = replace(self._specs[kind], source=lambda mgr: source())
            return
        self._extra.append(
            BlockSpec(
                kind=kind,
                title=None,
                section=SYSTEM,
                stability=FROZEN,
                cap=None,
                source=lambda mgr: source(),
            )
        )

    # ---- built-in block bodies ----

    def _environment_body(self) -> str:
        # Deferred import: the tools package pulls in agent.skills, so a top-level
        # import would be circular.
        from .tools import workspace

        # The run's workspace root wins here, so one process can serve several workspaces.
        root = self.state.workspace_root or workspace.WORKSPACE_ROOT
        names = "、".join(self.tool_names) if self.tool_names else "（无）"
        runtime = (
            f"{platform.system()} {platform.machine()} / Python {platform.python_version()}"
        )
        scratch = (
            "\n这是临时对话（从主对话拷来的上下文）：工作区在沙箱里只读，写入类工具不在表里。"
            "只做探索与回答；要改文件，请用户回主线对话去做。"
            if getattr(self.state, "scratch", False)
            else ""
        )
        return (
            f"工作目录：{root}\n"
            f"运行时：{runtime}；今天：{date.today().isoformat()}\n"
            f"可用工具：{names}\n"
            f"{scratch}"
            "\n"
            "Act, don't explain."
        )

    def _bootstrap_body(self) -> str | None:
        # Read once per run: frozen sources are collected only on the first compose,
        # which is also what keeps the frozen prefix byte-stable afterwards.
        root = self.state.workspace_root
        if not root:
            return None
        try:
            text = (Path(root) / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return text if text.strip() else None

    def _skill_always_body(self) -> str | None:
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
        return "以下技能已全文载入，无需再调用 load_skill：\n\n" + "\n\n".join(sections)

    def _skill_catalog_body(self) -> str:
        catalog = self.state.skills.catalog()
        return (
            f"{catalog or '（当前没有可用技能）'}\n\n"
            "Use load_skill to read the full instructions when a skill applies."
        )

    def _injected_body(self) -> str | None:
        # Text an extension injected for this run; it sits in the tail, so the frozen blocks the
        # prefix cache is built from stay the body's own.
        return "\n\n".join(self._injected) or None

    def _plan_body(self) -> str | None:
        # An empty plan renders a placeholder, so no plan at all means no block.
        if not self.state.todo.items:
            return None
        return self.state.todo.render()

    def _run_state_body(self) -> str:
        state = self.state
        return (
            f"第 {state.round} 轮；已执行 {state.tool_calls} 次工具调用；"
            f"被拒 {state.denials} 次（当前连击 {state.denial_streak}）；"
            f"压缩 {state.compactions} 次。"
        )

    # ---- assembly ----

    def _ordered_specs(self) -> list[BlockSpec]:
        return [*self._specs.values(), *self._extra]

    def _render_block(self, spec: BlockSpec) -> str | None:
        body = spec.source(self)
        if body is None or not body.strip():
            return None
        # The cap constrains the body, headings excluded: truncation hits content, the header stays.
        cap = getattr(self.budget, spec.cap) if isinstance(spec.cap, str) else spec.cap
        if cap is not None and len(body) > cap:
            body = body[:cap] + prompt.TRUNCATION_NOTE
        return spec.render(body)

    def _collect(self, stability: str) -> list[tuple[BlockSpec, str]]:
        collected: list[tuple[BlockSpec, str]] = []
        for spec in self._ordered_specs():
            if spec.stability != stability:
                continue
            text = self._render_block(spec)
            if text is not None:
                collected.append((spec, text))
        return collected

    def compose(self, *, injected: list[str] | None = None) -> ComposedRequest:
        """Compact and render; injected text joins the tail note, never the frozen blocks."""
        self._injected = [str(text) for text in (injected or []) if str(text).strip()]
        if self._system is None:
            # Freezing before compacting is what lets the compaction request carry this prefix.
            self._freeze_system()
        reports = self._compact()
        return self._assemble(reports)

    def system_prompt(self) -> str:
        """Render the system prompt alone, without compaction or tail, for baseline arms."""
        if self._system is None:
            self._freeze_system()
        assert self._system is not None  # _freeze_system always assigns
        return self._system

    def _freeze_system(self) -> None:
        blocks = self._collect(FROZEN)
        self._system = "\n\n".join(text for _spec, text in blocks)
        self._system_parts = {}
        for spec, text in blocks:
            self._system_parts[spec.kind] = (
                self._system_parts.get(spec.kind, 0) + len(text)
            )

    def render(self) -> ComposedRequest:
        """Re-render after a fallback compaction; the system prompt itself stays fixed."""
        if self._system is None:
            raise RuntimeError("render() 之前必须先 compose()")
        return self._assemble([])

    def _assemble(self, reports: list[CompactReport]) -> ComposedRequest:
        # The tail becomes one extra user message and is never written to the transcript.
        tail_blocks = self._collect(PER_ROUND)
        messages = self.transcript.as_messages()
        tail_chars = 0
        if tail_blocks:
            content = TAIL_HEADER + "\n\n" + "\n\n".join(
                text for _spec, text in tail_blocks
            )
            # `tail` marks a kernel-injected message: reasoning replay must not read it as the
            # start of a new turn (it is dropped from the wire by the protocol whitelist).
            message = {"role": "user", "content": content, "tail": True}
            messages.append(message)
            tail_chars = message_chars(message)

        parts = dict(self._system_parts)
        for spec, text in tail_blocks:
            parts[spec.kind] = parts.get(spec.kind, 0) + len(text)
        parts["history"] = self.transcript.estimate_chars()
        parts["messages"] = parts["history"] + tail_chars
        return ComposedRequest(
            system=self._system or "",
            messages=messages,
            parts=parts,
            reports=reports,
        )

    # ---- compaction: delegated to the single path in compaction.py ----

    def _run_compaction(self, *, force: bool) -> CompactReport | None:
        """The one place that hands the prefix over: /compact, the automatic trigger and the
        overflow fallback all send a request identical to the round's up to the cut."""
        return compact.run_compaction(
            transcript=self.transcript,
            state=self.state,
            config=self.config,
            chat=self.summarize,
            limits=self.budget,
            force=force,
            on_compaction=self.on_compaction,
            system=self.system_prompt(),
            tools=self.tool_schemas,
        )

    def _compact(self) -> list[CompactReport]:
        report = self._run_compaction(force=False)
        return [report] if report is not None else []

    def compact_now(self) -> CompactReport | None:
        """Force one compaction for /compact, skipping the trigger line and the once-per-run
        guard; None means there was no earlier history or the summary failed."""
        return self._run_compaction(force=True)

    # ---- the round's own model call: the overflow fallback lives beside compaction ----

    def call(self, request: ComposedRequest) -> Turn:
        """Send one composed request and return the turn; the caller asks for a Turn, not a retry.

        A PromptTooLongError compacts once and resends the re-rendered request, which is the same
        compaction the trigger and /compact use — only reached from the provider's own refusal.
        """
        self._note_prompt_parts(request)
        try:
            return self._send(request)
        except PromptTooLongError:
            if self.state.retried:
                raise
            self.state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            self.compact_now()
            request = self.render()
            self._note_prompt_parts(request)
            return self._send(request)

    def _send(self, request: ComposedRequest) -> Turn:
        if self.chat is None:  # pragma: no cover - only Run calls this, always via from_spec
            raise RuntimeError("ContextManager 没有 chat，无法发起模型调用")
        return self.chat(
            self.config,
            request.messages,
            system=request.system,
            tools=self.tool_schemas,
            max_tokens=self.max_tokens,
        )

    def _note_prompt_parts(self, request: ComposedRequest) -> None:
        """Record the three prompt parts; the assembler is what knows the system and tools shares."""
        self.state.record_prompt_parts(
            system=request.system_chars,
            tools=(
                len(json.dumps(self.tool_schemas, ensure_ascii=False))
                if self.tool_schemas
                else 0
            ),
            messages=request.messages_chars,
        )


#: Declaration order is render order and the cache discipline: stable blocks first, the per-round
#: blocks last in the messages tail. Sources live on ContextManager; this table declares.
CONTEXT_MAP: list[BlockSpec] = [
    BlockSpec(
        kind=INSTRUCTIONS,
        title=None,
        section=SYSTEM,
        stability=FROZEN,
        cap=None,
        source=lambda mgr: mgr.instructions,
    ),
    BlockSpec(
        kind=ENVIRONMENT,
        title="环境",
        section=SYSTEM,
        stability=FROZEN,
        cap=None,
        source=lambda mgr: mgr._environment_body(),
    ),
    BlockSpec(
        kind=BOOTSTRAP,
        title="工作区约定",
        section=SYSTEM,
        stability=FROZEN,
        cap="bootstrap_chars",
        source=lambda mgr: mgr._bootstrap_body(),
    ),
    BlockSpec(
        kind=SKILL_ALWAYS,
        title="常驻技能",
        section=SYSTEM,
        stability=FROZEN,
        cap="skill_always_chars",
        source=lambda mgr: mgr._skill_always_body(),
    ),
    BlockSpec(
        kind=SKILL_CATALOG,
        title="可用技能",
        section=SYSTEM,
        stability=FROZEN,
        cap=None,
        source=lambda mgr: mgr._skill_catalog_body(),
    ),
    BlockSpec(
        kind=INJECTED,
        title=None,
        section=TAIL,
        stability=PER_ROUND,
        cap=None,
        source=lambda mgr: mgr._injected_body(),
    ),
    BlockSpec(
        kind=PLAN,
        title="当前计划",
        section=TAIL,
        stability=PER_ROUND,
        cap=None,
        source=lambda mgr: mgr._plan_body(),
    ),
    BlockSpec(
        kind=RUN_STATE,
        title="运行状态",
        section=TAIL,
        stability=PER_ROUND,
        cap=None,
        source=lambda mgr: mgr._run_state_body(),
    ),
]
