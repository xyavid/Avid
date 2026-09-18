"""任务图只读视图（F1）。

任务的写入者有且只有 ``TaskStore``（agent 的六个任务工具），所以 API 里**没有**
改任务状态的端点——人类要改状态就走 ``/api/runs`` 让 agent 去调用工具，权限
闸门与审计因此不被绕过（设计文档 §6.1）。

**任务跟着工作区走**：任务库是 ``<工作区根>/.tasks/``（``tools/tasks.py`` 的
``store_for_root``）。这里以前直接用模块级 ``TASKS``（按进程 CWD 解析），于是
多工作区模式下任务板显示的是**另一个工作区**的任务——阶段 18 把"运行级工作区根"
推到了工具与循环，唯独漏了这一层。现在由调用方给出工作区，缺省是进程绑定的那个。

视图里派生两件 UI 需要的判断：

* ``can_start``：依赖是否都 completed（``tools/tasks.py`` 的同名库函数）；
* ``dependency_titles``：把 ``blockedBy`` 的 id 翻成标题，任务板不必再取一次。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ..tools import tasks as task_tools
from .errors import TaskCorrupt, TaskNotFound
from .workspaces import Workspace, WorkspaceRequired, WorkspaceService


class TaskService:
    def __init__(self, workspaces: WorkspaceService | None = None) -> None:
        self.workspaces = workspaces

    # ---------------- 工作区 ----------------

    def _workspace(self, selection: str | None) -> Workspace:
        """这次读哪个工作区的任务。

        缺省是**进程绑定的那个**（`Services` 必绑定一个，所以不存在"没有工作地点"）；
        显式给 ``workspace`` 时按 id/路径解析——多工作区模式下界面要能看别的工作区的
        任务板，而任务库不在会话库里，没法从会话反推。
        """
        if self.workspaces is None:
            raise WorkspaceRequired("任务视图需要一个工作区，但服务没有绑定工作区解析器")
        if selection:
            return self.workspaces.resolve(selection)
        default = self.workspaces.default
        if default is None:
            raise WorkspaceRequired("多工作区模式下读任务必须指定 workspace")
        return default

    def _store(self, selection: str | None) -> task_tools.TaskStore:
        return task_tools.store_for_root(Path(self._workspace(selection).root))

    # ---------------- 读 ----------------

    def list_tasks(self, workspace: str | None = None) -> list[dict]:
        """该工作区的全部任务（按 id 排序）。损坏项跳过并记 warning。"""
        store = self._store(workspace)
        with store.lock:
            found = task_tools.list_tasks(store)
            return [self._view(task, found, store) for task in found]

    def get_task(self, task_id: str, workspace: str | None = None) -> dict:
        store = self._store(workspace)
        with store.lock:
            task = task_tools.load_task(task_id, store)
            if task is None:
                raise TaskNotFound(f"没有这个任务：{task_id}")
            return self._view(task, task_tools.list_tasks(store), store)

    # ---------------- 内部 ----------------

    @staticmethod
    def _view(
        task: task_tools.Task,
        all_tasks: list[task_tools.Task],
        store: task_tools.TaskStore,
    ) -> dict:
        titles = {item.id: item.subject for item in all_tasks}
        try:
            incomplete = store.incomplete_of(task)
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
