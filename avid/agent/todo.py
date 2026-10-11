"""The whole-list TODO store: replacement is all-or-nothing, and each run owns its own list."""

from __future__ import annotations

from typing import Any, NoReturn

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
        """Replace the list from raw input; an invalid item raises and leaves the old list as is."""
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
