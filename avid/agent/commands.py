"""Session commands (`/compact`, `/rewind`, `/<skill name>`), parsed here once for CLI and Web.

Only a single-token `/name` enters the command namespace, since paths and free text must pass
through and an unmatched name must come back as unknown instead of reaching the model; `rewind`
registers its name only, because moving the branch tip and clearing the compaction cursor are the
caller's wiring and this module never imports the session package.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .compaction import CompactReport, ContextBudget, run_compaction
from .skills import SkillLoader, default_skills_dir

#: Command namespace: a single-token /name; paths (multiple slashes) and free text stay outside it.
_TOKEN = re.compile(r"^/([a-z][a-z0-9_-]*)$")

#: Registered commands; a skill name only resolves when it does not collide with one.
COMMANDS = ("compact", "rewind")

KIND_COMMAND = "command"
KIND_SKILL = "skill"
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class CommandMatch:
    """The parse result of one input: the name and what it belongs to."""

    name: str
    kind: str  # command | skill | unknown


def match_command(text: str, *, skill_names: Iterable[str] = ()) -> CommandMatch | None:
    """Parse one input as a command; None means it is ordinary user input."""
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
    """Skill names visible in this workspace, sorted."""
    loader = SkillLoader(default_skills_dir(workspace_root)).scan()
    return sorted(loader.skills)


def skill_text(name: str, *, workspace_root: str | None) -> str | None:
    """Full skill text; an unknown name returns None so the caller can list what exists."""
    loader = SkillLoader(default_skills_dir(workspace_root)).scan()
    if name not in loader.skills:
        return None
    return loader.load(name)


def help_text(*, workspace_root: str | None) -> str:
    """Hint for an unknown command: the available commands and workspace skills."""
    skills = skill_names(workspace_root=workspace_root)
    skill_part = "、".join(f"/{name}" for name in skills) if skills else "（当前没有技能）"
    return f"可用命令：/compact、/rewind；可用技能：{skill_part}"


def compact_session(
    *,
    history: list[dict[str, Any]],
    config: Any,
    summarize: Callable[..., Any],
    workspace_root: str | None = None,
    on_compaction: Callable[[dict[str, Any], int], None] | None = None,
) -> CompactReport | None:
    """Force one compaction of the session history for /compact, skipping the trigger line and the
    once-per-run guard; None means no earlier history exists to summarize."""
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
