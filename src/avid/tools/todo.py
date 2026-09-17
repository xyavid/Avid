"""todo_write：带状态的 TODO 列表。

状态由 ``RunState`` 显式持有并传入（原来是 ContextVar）——工具不再自己去找状态，
因此"哪个工具需要运行状态"是一个可枚举、可断言的事实（见 ``execution.STATEFUL_TOOLS``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:  # 只为类型标注；运行时导入会成环（state.py 要 import 本模块）
    from ..state import RunState

# 连续多少轮没更新 TODO 就提醒一次。调阈值只改这一个常量。
TODO_REMINDER_AFTER_ROUNDS = 3

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


def build_reminder(todo: TodoList, rounds: int) -> str:
    return (
        f"[提醒] 已经连续 {rounds} 轮没有更新 TODO 列表。"
        "如果任务还需要多步，请用 todo_write 重新提交完整列表并更新进度；"
        "如果已经做完，请在最终答复里说明。\n当前列表：\n" + todo.render()
    )
