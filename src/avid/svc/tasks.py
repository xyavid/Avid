"""任务图只读视图（F1）。

任务的写入者有且只有 ``TaskStore``（agent 的六个任务工具），所以 API 里**没有**
改任务状态的端点——人类要改状态就走 ``/api/runs`` 让 agent 去调用工具，权限
闸门与审计因此不被绕过（设计文档 §6.1）。

视图里派生两件 UI 需要的判断：

* ``can_start``：依赖是否都 completed（``tools/tasks.py`` 的同名库函数）；
* ``dependency_titles``：把 ``blockedBy`` 的 id 翻成标题，任务板不必再取一次。
"""

from __future__ import annotations

from dataclasses import asdict

from ..tools import tasks as task_tools
from .errors import TaskCorrupt, TaskNotFound


class TaskService:
    def list_tasks(self) -> list[dict]:
        """全部任务的只读视图（按 id 排序）。损坏项跳过并记 warning。"""
        with task_tools.TASKS.lock:
            found = task_tools.list_tasks()
            return [self._view(task, found) for task in found]

    def get_task(self, task_id: str) -> dict:
        with task_tools.TASKS.lock:
            task = task_tools.load_task(task_id)
            if task is None:
                raise TaskNotFound(f"没有这个任务：{task_id}")
            return self._view(task, task_tools.list_tasks())

    # ---------------- 内部 ----------------

    @staticmethod
    def _view(task: task_tools.Task, all_tasks: list[task_tools.Task]) -> dict:
        titles = {item.id: item.subject for item in all_tasks}
        try:
            incomplete = task_tools.incomplete_dependencies(task)
        except task_tools.TaskError as exc:  # 依赖文件损坏
            raise TaskCorrupt(str(exc)) from exc
        view = asdict(task)
        view.update(
            {
                "can_start": not incomplete,
                "blocked": task.status == "pending" and bool(incomplete),
                "incomplete_dependencies": incomplete,
                "dependency_titles": {
                    dep: titles.get(dep, task_tools.MISSING) for dep in task.blockedBy
                },
            }
        )
        return view


__all__ = ["TaskService"]
