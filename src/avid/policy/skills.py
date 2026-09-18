"""技能系统：目录常驻，全文按需。

系统提示里只放技能目录（name + description，每个一行），完整说明要模型主动调
``load_skill`` 才进上下文——技能变多也不会撑爆 system prompt。

注册表按运行隔离：``agent_loop`` 每次运行新建一个 ``SkillLoader`` 并绑定到
ContextVar，所以磁盘上的技能目录一变，下次运行的 system prompt 就是新的。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("avid.policy.skills")

SKILLS_DIR = Path.cwd() / "skills"

AGENT_INSTRUCTIONS = (
    "你是 Avid，一个能自主调用工具完成任务的 agent。"
    "需要外部信息或动作时调用工具；信息足够时直接给出答案。"
    "任务需要三步以上时，先用 todo_write 列出计划再逐步执行，"
    "每完成一步就重新提交整份列表并更新状态。"
)


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """极简 frontmatter：只认单行 ``key: value``，不引 YAML 依赖。"""
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

    return {}, text  # 没有收尾的 ---：当作没有 frontmatter


def _first_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lstrip("#").strip()
    return ""


class SkillLoader:
    """扫描 ``skills_dir/<name>/SKILL.md``，维护 name → 技能 的注册表。"""

    def __init__(self, skills_dir: str | Path | None = None) -> None:
        self.skills_dir = Path(skills_dir) if skills_dir is not None else SKILLS_DIR
        self.skills: dict[str, dict[str, str]] = {}

    def scan(self) -> "SkillLoader":
        """重建注册表；非文件、或 resolve 后不在 skills_root 内的条目一律跳过。"""
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
            }

        logger.info(
            "已加载 %d 个技能：%s",
            len(self.skills),
            "、".join(sorted(self.skills)) or "无",
        )
        return self

    def catalog(self) -> str:
        """只输出名称与描述——这一份是可以常驻上下文的部分。"""
        return "\n".join(
            f"- {name}: {self.skills[name]['description']}"
            for name in sorted(self.skills)
        )

    def build_system_prompt(
        self, instructions: str = AGENT_INSTRUCTIONS, workdir: str | None = None
    ) -> str:
        # 延迟导入：tools 包要 import tools/skill.py，而它要 import 本模块。
        from ..tools import workspace

        # 运行级工作区根优先（一个进程可以服务多个工作区），否则用进程默认根。
        root = workdir or workspace.WORKSPACE_ROOT
        return (
            f"{instructions}\n"
            f"工作目录：{root}\n"
            "Act, don't explain.\n\n"
            f"## 可用技能\n{self.catalog() or '（当前没有可用技能）'}\n\n"
            "Use load_skill to read the full instructions when a skill applies."
        )

    def load(self, name: str) -> str:
        """按注册表的 key 查，**不当作文件路径**——路径样式的输入只会是未命中。"""
        skill = self.skills.get(name)
        if skill is not None:
            return skill["content"]

        available = ", ".join(sorted(self.skills)) or "none"
        return f"Error: Unknown skill '{name}'. Available: {available}"
