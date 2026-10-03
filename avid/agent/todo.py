"""todo_write: a whole-list TODO store whose state is supplied by the caller on every call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

from .tools.registry import tool

# Type-only import: importing RunState at runtime would cycle back into this module.
if TYPE_CHECKING:
    from ..runtime.state import RunState

VALID_STATUSES = ("pending", "in_progress", "completed")

# Single-character marks rendered for each status in the list output.
_MARKS = {"completed": "x", "in_progress": "~", "pending": " "}


def _bad(message: str) -> NoReturn:
    raise ValueError(message)


class TodoList:
    """A TODO list replaced as a whole, with validation that is all-or-nothing."""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def replace(self, raw: Any) -> None:
        """Replace the list from raw input; an invalid item raises and leaves the old list untouched."""
        if not isinstance(raw, list):
            _bad("todos 必须是数组")

        cleaned: list[dict[str, str]] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                _bad(f"第 {index} 项不是对象")
            content = item.get("content")
            status = item.get("status")
            if not isinstance(content, str) or not content.strip():
                _bad(f"第 {index} 项的 content 不能为空")
            if status not in VALID_STATUSES:
                _bad(
                    f"第 {index} 项的 status 非法：{status!r}；"
                    f"只能是 {'、'.join(VALID_STATUSES)}"
                )
            cleaned.append({"content": content.strip(), "status": status})

        # At most one item may be in progress, so the model always has a single current step.
        active = sum(1 for item in cleaned if item["status"] == "in_progress")
        if active > 1:
            _bad(
                f"同时只能有一项 in_progress，当前有 {active} 项；"
                "请把其余项改成 pending 或 completed"
            )

        self.items = cleaned

    def counts(self) -> dict[str, int]:
        return {
            status: sum(1 for item in self.items if item["status"] == status)
            for status in VALID_STATUSES
        }

    def summary(self) -> str:
        counts = self.counts()
        return "、".join(f"{name} {counts[name]}" for name in VALID_STATUSES)

    def render(self) -> str:
        if not self.items:
            return "（列表为空）"
        return "\n".join(
            f"[{_MARKS[item['status']]}] {number}. {item['content']}"
            for number, item in enumerate(self.items, start=1)
        )


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
        return f"错误：{exc}"

    if not state.todo.items:
        return "已清空 TODO 列表。"
    return (
        f"已更新 TODO（{len(state.todo.items)} 项：{state.todo.summary()}）\n"
        f"{state.todo.render()}"
    )
