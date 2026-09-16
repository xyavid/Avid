"""load_skill：技能系统的第二层——按需取回某个技能的完整说明。

技能目录（只有名称与描述）常驻在 system prompt 里；完整说明动辄几十行，
所以只在模型判断"这个技能用得上"时才通过本工具进上下文。
"""

from __future__ import annotations

from typing import Any

from ..skill_loader import current_skills


def load_skill(args: dict[str, Any]) -> str:
    loader = current_skills()
    if loader is None:
        return "错误：当前没有可用的技能注册表"

    name = str(args.get("name", "")).strip()
    if not name:
        return "错误：缺少参数 name"

    return loader.load(name)
