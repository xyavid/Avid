"""技能系统：目录常驻，全文按需。

技能目录（name + description，每个一行）由 ``runtime/context_manager.py`` 渲染进
系统提示词，完整说明要模型主动调 ``load_skill`` 才进上下文——技能变多也不会撑爆
system prompt。本模块只负责扫描与查询：谁在装配提示词，谁去找装配器。

注册表按运行隔离：``agent_loop`` 每次运行新建一个 ``SkillLoader``，所以磁盘上的
技能目录一变，下次运行的 system prompt 就是新的。

**技能目录在构造时解析，不在 import 时**：以前是模块级 `SKILLS_DIR = Path.cwd() /
"skills"`，于是从别的目录启动、或一个进程服务多个工作区时，技能目录永远是"启动
那一刻的 cwd"（阶段 18 把运行级工作区根推到了所有落点，这里是漏掉的一个）。
默认值是 `<运行级工作区根>/skills`，没有工作区根时才回落到进程 cwd。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("avid.policy.skills")

SKILLS_SUBDIR = "skills"


def default_skills_dir(root: str | Path | None = None) -> Path:
    """默认技能目录：`<root>/skills`；没给 root 才回落到进程 cwd。

    调用时求值——这是本函数存在的全部理由（见模块 docstring）。
    """
    return (Path(root) if root is not None else Path.cwd()) / SKILLS_SUBDIR


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
        self.skills_dir = (
            Path(skills_dir) if skills_dir is not None else default_skills_dir()
        )
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

    def load(self, name: str) -> str:
        """按注册表的 key 查，**不当作文件路径**——路径样式的输入只会是未命中。"""
        skill = self.skills.get(name)
        if skill is not None:
            return skill["content"]

        available = "、".join(sorted(self.skills)) or "（无）"
        # 工具结果的失败文案统一以「错误：」开头（tools/ 的约定，模型据此判断失败）；
        # 这里以前返回英文 "Error: …"，而它正是模型看到的工具结果。
        return f"错误：没有这个技能「{name}」。可用：{available}"
