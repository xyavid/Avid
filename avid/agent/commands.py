"""会话内命令：`/compact` 与 `/<技能名>`（内核单点解析，CLI 与 Web 共用）。

规则：`/name` **单 token**（名字形如 `[a-z][a-z0-9_-]*`，无嵌套斜杠与点）才落
进命令命名空间——`/home/x` 这类路径与含空白的文本原样透传。落在命名空间内：
命中已注册命令或当前工作区的技能名 → 命令；否则 unknown（调用方给提示，
不发给模型）。

执行体也在这里：`compact_session` 对一段历史强制压缩一次（摘要调用由调用方
注入），`skill_text` 取技能全文（写入会话由调用方落库）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .compaction import CompactReport, ContextBudget, run_compaction
from .skills import SkillLoader, default_skills_dir

#: 命令命名空间：单 token 的 /name；路径（多段斜杠）与散文本都落在命名空间外。
_TOKEN = re.compile(r"^/([a-z][a-z0-9_-]*)$")

#: 已注册命令（v1 只有压缩；技能名不与它们冲突时按技能解析）。
COMMANDS = ("compact",)

KIND_COMMAND = "command"
KIND_SKILL = "skill"
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class CommandMatch:
    """一次输入的解析结果：name 与它的归属。"""

    name: str
    kind: str  # command | skill | unknown


def match_command(text: str, *, skill_names: Iterable[str] = ()) -> CommandMatch | None:
    """把一条输入解析成命令；None = 不是命令，按普通用户输入处理。"""
    found = _TOKEN.match(text.strip())
    if found is None:
        return None
    name = found.group(1)
    if name in COMMANDS:
        return CommandMatch(name, KIND_COMMAND)
    if name in set(skill_names):
        return CommandMatch(name, KIND_SKILL)
    return CommandMatch(name, KIND_UNKNOWN)


def skill_names(*, workspace_root: str | None) -> list[str]:
    """当前工作区可见的技能名（排序）。"""
    loader = SkillLoader(default_skills_dir(workspace_root)).scan()
    return sorted(loader.skills)


def skill_text(name: str, *, workspace_root: str | None) -> str | None:
    """技能全文；未知技能返回 None（调用方给可用清单）。"""
    loader = SkillLoader(default_skills_dir(workspace_root)).scan()
    if name not in loader.skills:
        return None
    return loader.load(name)


def help_text(*, workspace_root: str | None) -> str:
    """未知命令时的提示：可用命令与当前工作区的技能。"""
    skills = skill_names(workspace_root=workspace_root)
    skill_part = "、".join(f"/{name}" for name in skills) if skills else "（当前没有技能）"
    return f"可用命令：/compact；可用技能：{skill_part}"


def compact_session(
    *,
    history: list[dict[str, Any]],
    config: Any,
    summarize: Callable[..., Any],
    workspace_root: str | None = None,
    on_compaction: Callable[[dict[str, Any], int], None] | None = None,
) -> CompactReport | None:
    """对会话当前历史强制压缩一次（/compact 的执行体）。

    force=True：跳过触发线与每运行一次的守护——用户明确要求压缩就压缩。
    摘要调用由调用方注入（CLI 用非流式 chat；Web 同）。历史不足一个保留窗口
    时返回 None（没有「更早历史」可摘要）。
    """
    from .state import RunState
    from .transcript import Transcript

    state = RunState(workspace_root=workspace_root)
    transcript = Transcript(list(history))
    return run_compaction(
        transcript=transcript,
        state=state,
        config=config,
        chat=summarize,
        limits=ContextBudget(),
        force=True,
        on_compaction=on_compaction,
    )
