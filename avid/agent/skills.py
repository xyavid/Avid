"""Skill system: keeps one-line catalog entries resident and loads full skill text on demand."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("avid.agent.skills")

SKILLS_SUBDIR = "skills"

# frontmatter `always:` 认可的写法；其余值一律视为不常驻。
_ALWAYS_TRUTHY = {"true", "yes", "1", "on"}


def default_skills_dir(root: str | Path | None = None) -> Path:
    """Return `<root>/skills`, falling back to the process cwd when no root is given.

    Resolved at call time, so a long-lived process serving several workspaces stays correct.
    """
    return (Path(root) if root is not None else Path.cwd()) / SKILLS_SUBDIR


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a minimal leading block of single-line `key: value` pairs, without a YAML dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    meta: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1 :])
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()

    return {}, text  # no closing ---, so the file is treated as having no frontmatter


def _first_line(body: str) -> str:
    # Fallback description: the first non-blank line, with any heading marks stripped.
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lstrip("#").strip()
    return ""


class SkillLoader:
    """Scans `<skills_dir>/<name>/SKILL.md` and maintains a name to skill registry."""

    def __init__(self, skills_dir: str | Path | None = None) -> None:
        self.skills_dir = (
            Path(skills_dir) if skills_dir is not None else default_skills_dir()
        )
        # `always` is a bool among string fields, so the value type stays loose.
        self.skills: dict[str, dict[str, Any]] = {}

    def scan(self) -> "SkillLoader":
        """Rebuild the registry, skipping non-files and entries that resolve outside the skills root."""
        self.skills = {}
        root = self.skills_dir.resolve()
        if not root.is_dir():
            logger.info("技能目录不存在：%s", root)
            return self

        for path in sorted(root.glob("*/SKILL.md")):
            if not path.is_file():
                continue

            resolved = path.resolve()
            if root not in resolved.parents:
                logger.warning("跳过 skills_root 之外的条目：%s", path)
                continue

            text = resolved.read_text(encoding="utf-8", errors="replace")
            meta, body = _split_frontmatter(text)
            name = (meta.get("name") or path.parent.name).strip()
            description = (meta.get("description") or _first_line(body)).strip()
            self.skills[name] = {
                "name": name,
                "description": description,
                "content": text,
                "always": meta.get("always", "").strip().lower() in _ALWAYS_TRUTHY,
                "body": body,
            }

        logger.info(
            "已加载 %d 个技能：%s",
            len(self.skills),
            "、".join(sorted(self.skills)) or "无",
        )
        return self

    def catalog(self) -> str:
        """Render the name and description lines that stay resident in the system prompt.

        Always skills are left out: their full text already sits in the prompt, so a
        catalog entry would only invite a pointless load_skill call.
        """
        return "\n".join(
            f"- {name}: {self.skills[name]['description']}"
            for name in sorted(self.skills)
            if not self.skills[name]["always"]
        )

    def always_bodies(self) -> list[tuple[str, str]]:
        """Return sorted (name, body) for always skills, frontmatter stripped.

        Uncapped: how much of this may enter the system prompt is a prompt-budget
        decision, made by the assembler (context_manager) via policy.prompt.
        """
        return [
            (name, self.skills[name]["body"])
            for name in sorted(self.skills)
            if self.skills[name]["always"]
        ]

    def load(self, name: str) -> str:
        """Look a skill up by registry key only, so a path-shaped argument is always a miss."""
        skill = self.skills.get(name)
        if skill is not None:
            return skill["content"]

        available = "、".join(sorted(self.skills)) or "（无）"
        # Tool failures open with the Chinese error marker by convention, since the model reads this text.
        return f"错误：没有这个技能「{name}」。可用：{available}"
