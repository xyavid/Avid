"""Session commands (`/compact`, `/rewind`, `/<skill name>`), parsed here once for CLI and Web.

Only a single-token `/name` enters the command namespace, since paths and free text must pass
through and an unmatched name must come back as unknown instead of reaching the model; both command
bodies are the caller's wiring (moving the branch tip, clearing the cursor, handing the compaction
its assembled context), so this module never imports the session package and executes neither.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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
