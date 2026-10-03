"""load_skill: fetches the full text of one skill on demand.

The system prompt carries only names and descriptions, so a full text reaches the context
only when the model asks for it here.

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import tool

if TYPE_CHECKING:  # runtime import would be circular (state.py imports the skill loader)
    from ..runtime import RunState


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
    # Reads the registry snapshot only, which is why this call is concurrency safe.
    concurrency="safe",
)
def load_skill(args: dict[str, Any], *, state: "RunState") -> str:
    """Returns the full text of the named skill, or an error line when the name is missing."""
    name = str(args.get("name", "")).strip()
    if not name:
        return "错误：缺少参数 name"

    return state.skills.load(name)
