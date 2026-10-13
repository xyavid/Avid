"""todo_write: the model-facing declaration; the store it replaces lives in ``agent/todo.py``.

Declaring it here rather than in the store's module is load-bearing: the tools package snapshots
its tables while it is being imported, so a declaration that reaches back into the agent layer
would register after the snapshot and be missing from every derived table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import tool

if TYPE_CHECKING:
    from ..state import RunState


@tool(
    name="todo_write",
    description="整份替换当前任务的 TODO 列表，用来把多步任务显式计划出来并跟踪进度。"
    "每次调用都要提交【完整】列表，不是增量；开始多步任务前先调用一次，"
    "之后每完成一步就更新对应项的状态并重新提交整份列表。"
    "只有一步、或不需要跟踪进度时不必调用。",
    properties={
        "todos": {
            "type": "array",
            "description": "完整 TODO 列表，按执行顺序排列；空数组表示清空。",
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "这一步要做什么，一句话。",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "该步状态：未开始 / 进行中 / 已完成。",
                    },
                },
                "required": ["content", "status"],
                "additionalProperties": False,
            },
        }
    },
    required=("todos",),
    # The TODO list is shared mutable state, so calls into this tool must not interleave.
    concurrency="exclusive",
)
def todo_write(args: dict[str, Any], *, state: "RunState") -> str:
    """Replace the whole TODO list and return either the rendered list or a validation error."""
    try:
        state.todo.replace(args.get("todos"))
    except ValueError as exc:
        # Nothing is published on a rejected update: the previous plan is still the plan.
        return f"错误：{exc}"
    state.publish_plan()

    if not state.todo.items:
        return "已清空 TODO 列表。"
    return (
        f"已更新 TODO（{len(state.todo.items)} 项：{state.todo.summary()}）\n"
        f"{state.todo.render()}"
    )
