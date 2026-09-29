"""todo_write：带状态的 TODO 列表。

状态由 ``RunState`` 显式持有并传入（原来是 ContextVar）——工具不再自己去找状态，
因此"哪个工具需要运行状态"是一个可枚举、可断言的事实（见 ``execution.STATEFUL_TOOLS``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

from ..tools.registry import tool

if TYPE_CHECKING:  # 只为类型标注；运行时导入会成环（state.py 要 import 本模块）
    from ..runtime.state import RunState

VALID_STATUSES = ("pending", "in_progress", "completed")

_MARKS = {"completed": "x", "in_progress": "~", "pending": " "}


def _bad(message: str) -> NoReturn:
    raise ValueError(message)


class TodoList:
    """一份 TODO 列表。整体替换、原子校验。"""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def replace(self, raw: Any) -> None:
        """用 raw 整体替换当前列表。

        任一项不合法就抛 ValueError，且**旧列表一字不动**——半接受的列表比
        拒绝更难排查。
        """
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
    # 共享可变状态（待办清单）。
    concurrency="exclusive",
)
def todo_write(args: dict[str, Any], *, state: "RunState") -> str:
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
