"""Context 组装：一张声明表（CONTEXT_MAP）+ 一个装配引擎，决定模型每轮看到什么。

## 请求的三部分

- **system**：frozen 块首轮拼接后逐字节冻结（结构上保住 provider 前缀缓存），
  外加 UserPromptSubmit hook 的 injected 文本；
- **messages**：transcript 历史（可被压缩阶梯改写，见 compaction.py）+ 一条每轮
  重建的 tail 便签（TAIL_HEADER 头 + per_round 块）；tail 永不写入 transcript；
- **tools**：本次运行的 schema 数组，每轮不变。

## 块清单（CONTEXT_MAP，声明顺序即渲染顺序）

| kind          | 去哪   | 生命周期 | 上限        | 数据来源 |
|---------------|--------|----------|-------------|----------|
| instructions  | system | frozen   | —           | 调用方传入，或 prompt.DEFAULT_INSTRUCTIONS |
| environment   | system | frozen   | —           | 工作目录 / 运行时 / 日期 / 本次工具名 |
| bootstrap     | system | frozen   | 16,000 字符 | 工作区 AGENTS.md（缺失即整块缺席） |
| skill_always  | system | frozen   | 16,000 字符 | always 技能全文（单篇 8k，超限按名字序跳过） |
| skill_catalog | system | frozen   | —           | 技能目录（name: description 行） |
| plan          | tail   | per_round| —           | TODO（无计划即整块缺席） |
| run_state     | tail   | per_round| —           | 轮次 / 工具调用 / 被拒 / 压缩计数 |

扩展点：`register_block`（追加整条声明）或 `register_source`（替换已有 kind 的
文本源）。压缩阶梯在 compaction.py：compose() 每轮先跑一遍，再装配。

## 缓存纪律（KV-cache 排序，CONTEXT_MAP 的排序依据）

固定的、重复出现的块放前面，易变的放最后——frozen 块拼接出的 system 前缀
逐字节稳定，是缓存命中的部分。唯一允许的例外是 tail 便签：运行状态这类
per_round 内容按功能原理必须贴近对话末尾（锚定注意力），它们也确实只出现
在 messages 的最后一条里。新增块时按同一条纪律选 section/stability：内容
一次定型选 frozen、每轮变化选 per_round 且要能回答"为什么它必须在末尾"。
压缩阶梯改写 transcript 中段会击穿该点之后的缓存——那是 compaction.py 的
频率/幅度权衡（clear_at_least 语义），不是组装层的排序问题。
"""

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
from . import prompt
from .compaction import CompactReport, ContextBudget
from .state import RunState

logger = logging.getLogger("avid.agent.context")

#: 块的去向：system 进冻结前缀，tail 进每轮重建的便签（位置与生命周期一一对应）。
SYSTEM = "system"
TAIL = "tail"

#: 块的生命周期：frozen 首轮定型，per_round 每轮重新收集。
FROZEN = "frozen"
PER_ROUND = "per_round"

#: 内置块 kind；register_block/register_source 可扩展新块，无需改这里。
INSTRUCTIONS = "instructions"
ENVIRONMENT = "environment"
BOOTSTRAP = "bootstrap"
SKILL_ALWAYS = "skill_always"
SKILL_CATALOG = "skill_catalog"
INJECTED = "injected"  # 非声明块：UserPromptSubmit 注入的 system 文本
PLAN = "plan"
RUN_STATE = "run_state"

# 固定 tail 头，让模型与日志都能分辨这不是用户输入。
TAIL_HEADER = "[上下文] 以下是本次请求附带的运行时上下文，不是用户输入。"


@dataclass(frozen=True)
class BlockSpec:
    """一个上下文块的完整声明：去哪、何时定型、上限多少、文本从哪来。

    source 拿到 ContextManager（读 state / instructions / tool_names），返回正文
    文本；返回 None 表示本块缺席。标题渲染为 `## {title}\\n正文`，title 为 None
    时不加标题（instructions 用）。
    """

    kind: str
    title: str | None
    section: str
    stability: str
    # None = 不设上限；int = 正文字符数；str = ContextBudget 的字段名（按运行预算解析）。
    # 上限约束正文，标题不计入。
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
    """每轮调用的装配引擎：块清单见 CONTEXT_MAP，压缩编排委托 compaction.py。"""

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
        # 调用方的指令覆盖（评测 / 子代理各自携带）；None 回落到默认词表。
        self.instructions = (
            instructions if instructions is not None else prompt.DEFAULT_INSTRUCTIONS
        )
        self.tool_names = list(tool_names)
        self.budget = budget or ContextBudget()
        self.summarize = summarize
        # ④/⑤ 替换历史后的落盘钩子（summary 消息, keep 条数）；不接则压缩只在内存生效。
        self.on_compaction = on_compaction
        self._specs: dict[str, BlockSpec] = {spec.kind: spec for spec in CONTEXT_MAP}
        self._extra: list[BlockSpec] = []
        # The system prompt is frozen on the first compose so the provider prefix cache stays valid.
        self._system: str | None = None
        self._system_parts: dict[str, int] = {}

    # ---- 扩展点 ----

    def register_block(self, spec: BlockSpec) -> None:
        """追加一条自定义块声明。"""
        self._extra.append(spec)

    def register_source(self, kind: str, source: Callable[[], str | None]) -> None:
        """替换已有 kind 的文本源；kind 不在声明表里则追加为 frozen system 块。

        便捷源的签名是零参函数；声明表里的 source 都拿 ContextManager。
        """
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

    # ---- 各内置块的正文 ----

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
        return (
            f"工作目录：{root}\n"
            f"运行时：{runtime}；今天：{date.today().isoformat()}\n"
            f"可用工具：{names}\n"
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

    # ---- 装配 ----

    def _ordered_specs(self) -> list[BlockSpec]:
        return [*self._specs.values(), *self._extra]

    def _render_block(self, spec: BlockSpec) -> str | None:
        body = spec.source(self)
        if body is None or not body.strip():
            return None
        # 上限约束正文（标题不计入），与旧语义一致：截断发生在内容上，块头保留。
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
        blocks = self._collect(FROZEN)
        texts = [text for _spec, text in blocks]
        texts += [str(text) for text in injected if str(text).strip()]
        self._system = "\n\n".join(texts)
        self._system_parts = {}
        for spec, text in blocks:
            self._system_parts[spec.kind] = (
                self._system_parts.get(spec.kind, 0) + len(text)
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
        tail_blocks = self._collect(PER_ROUND)
        messages = self.transcript.as_messages()
        tail_chars = 0
        if tail_blocks:
            content = TAIL_HEADER + "\n\n" + "\n\n".join(
                text for _spec, text in tail_blocks
            )
            message = {"role": "user", "content": content}
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

    # ---- 压缩编排：委托给 compaction.py（43 在那里重构阶梯本身） ----

    def _compact(self) -> list[CompactReport]:
        return compact.compose_ladder(
            transcript=self.transcript,
            state=self.state,
            config=self.config,
            limits=self.budget,
            summarize=self.summarize,
            on_compaction=self.on_compaction,
        )

    def reactive(self) -> CompactReport | None:
        return compact.reactive_pass(
            transcript=self.transcript,
            state=self.state,
            config=self.config,
            summarize=self.summarize,
            on_compaction=self.on_compaction,
        )


#: 声明顺序即渲染顺序，也是缓存纪律的落点：稳定的在前（instructions 最静态），
#: 易变的（tail 两块）在 messages 末尾。来源方法长在 ContextManager 上，这里只做声明。
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
