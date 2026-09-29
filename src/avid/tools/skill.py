"""load_skill：技能系统的第二层——按需取回某个技能的完整说明。

技能目录（只有名称与描述）常驻在 system prompt 里；完整说明动辄几十行，
所以只在模型判断"这个技能用得上"时才通过本工具进上下文。

注册表由 ``RunState`` 显式传入（原来是 ContextVar）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import tool

if TYPE_CHECKING:  # 运行时导入会成环（state.py 要 import skill_loader）
    from ..runtime.state import RunState


@tool(
    name="load_skill",
    description="读取某个技能的完整说明。可用技能列在系统提示的「可用技能」里。"
    "当手上的任务命中某个技能时，先调它把完整步骤读出来再动手——"
    "目录里只有一句话，真正的操作步骤和坑都在全文里。",
    properties={
        "name": {
            "type": "string",
            "description": "技能名，必须与技能目录里列出的名字完全一致。",
        }
    },
    required=("name",),
    # 按注册表读技能全文；注册表是只读快照。
    concurrency="safe",
)
def load_skill(args: dict[str, Any], *, state: "RunState") -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return "错误：缺少参数 name"

    return state.skills.load(name)
