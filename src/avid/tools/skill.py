"""load_skill：技能系统的第二层——按需取回某个技能的完整说明。

技能目录（只有名称与描述）常驻在 system prompt 里；完整说明动辄几十行，
所以只在模型判断"这个技能用得上"时才通过本工具进上下文。

注册表由 ``RunState`` 显式传入（原来是 ContextVar）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 运行时导入会成环（state.py 要 import skill_loader）
    from ..runtime.state import RunState


def load_skill(args: dict[str, Any], *, state: "RunState") -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return "错误：缺少参数 name"

    return state.skills.load(name)
