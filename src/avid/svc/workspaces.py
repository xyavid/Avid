"""工作区服务：注册表的 HTTP 面，以及"按工作区取会话仓库"。

一个进程要能服务多个工作区：会话库不再是进程级的一个目录，而是每个工作区各自的
``<root>/.avid/sessions``。这里集中回答两个问题——**有哪些工作区**、**这个会话属于哪一个**。

两个概念分开：

* **进程绑定一个工作地点**（``default``）：``capabilities`` 与界面的预选值都取自它。
  任何装配方式（``Services(root=...)`` / ``workspace_root=...`` / 默认的当前目录）都
  必然有一个——不存在"没有工作地点"的进程。它**不进注册表**：启动与日常使用都不写盘，
  注册表只由用户的显式动作写入（``avid workspace add`` / ``POST /api/workspaces``）。
  未登记不影响可解析——id 由根目录派生，``resolve`` 先认绑定值再看注册表。
* **建会话必须显式指定工作区**：``resolve(None)`` 一律 ``WorkspaceRequired``（400）。
  进程自己的绑定值只是**预选项**，不是"可以省略"的默认值：省略会让归属取决于
  服务端状态而不是请求，而归属是会话的不可变事实，不该那样决定。

仓库按工作区缓存：同一个工作区里的会话句柄独占由 ``JsonlSessionRepo`` 自己保证，
缓存只是让"运行中刷新页面"复用同一个仓库实例（阶段 15 的 I3 前提）。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..session import JsonlSessionRepo, SessionMetadata
from ..workspaces import (
    Workspace,
    WorkspaceError,
    WorkspaceRegistry,
    derive_id,
    sessions_root,
)
from .errors import (
    PickerBusy,
    ServiceError,
    SessionNotFound,
    WorkspaceExists,
)
from .errors import (
    PickerFailed as PickerFailedError,
)
from .errors import (
    PickerUnavailable as PickerUnavailableError,
)
from .picker import PickerError, PickerFailed, PickerUnavailable, pick_directory

logger = logging.getLogger("avid.svc.workspaces")


class WorkspaceRequired(ServiceError):
    """多工作区模式下没有指定工作区。"""

    code = "workspace_required"
    status = 400


class WorkspaceMissing(ServiceError):
    """指定的工作区不存在／未登记。"""

    code = "workspace_not_found"
    status = 404


class WorkspaceInvalid(ServiceError):
    """工作区参数本身不合法（目录不存在、不是目录、模式名不认识）。"""

    code = "workspace_invalid"
    status = 400


class WorkspaceBound(ServiceError):
    """想删的是**进程绑定的那个工作地点**：它不出现在注册表里，永远在候选列表最前，
    删了也还在（候选 = 绑定值 + 注册表），所以只能拒，而不是假装删成功。"""

    code = "workspace_bound"
    status = 409


# 归属缓存的 TTL（秒）。取值小是刻意的：缓存只是省一次全库扫描，不是权威。
SESSION_LOOKUP_TTL_SECONDS = 5.0


class WorkspaceService:
    def __init__(
        self,
        registry: WorkspaceRegistry,
        *,
        default: Workspace | None = None,
        default_sessions_root: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.default = default
        # 调用方直接给会话库路径时用它（测试与 `Services(root=...)` 不必先造出
        # `<root>/.avid/sessions` 这层目录）。
        self.default_sessions_root = (
            Path(default_sessions_root) if default_sessions_root is not None else None
        )
        self._repos: dict[str, JsonlSessionRepo] = {}
        # 会话 → 归属的短期缓存。定位一个会话要扫"每个工作区 × 每个会话文件头"
        # （O(工作区数 × 会话数)），而它每次运行启动、每次读会话都要付一次。
        # TTL 很短：别的进程新建的会话最多晚这么久可见；本进程的建/删显式失效。
        self._lookup: dict[str, tuple[float, Workspace, SessionMetadata]] = {}
        # 一次只允许一个对话框：第二个窗口会盖住第一个，用户会以为界面卡死。
        self._pick_lock = threading.Lock()

    # ---------------- 查询 ----------------

    def workspaces(self) -> list[Workspace]:
        """**候选**工作区：默认工作区排在最前，其余按最近使用。已删除（墓碑）的不在内。"""
        items = self.registry.list()
        if self.default is None:
            return items
        return [self.default, *(ws for ws in items if ws.id != self.default.id)]

    def known_workspaces(self) -> list[Workspace]:
        """**所有还能找到数据的**工作区 = 候选 + 墓碑（已删除的）。

        为什么列举/定位会话要用它而不是 `workspaces()`：会话库在各自工作区的
        ``<root>/.avid/sessions``，删掉一个工作区（把它从候选里摘掉）之后，它下面的会话
        仍然是真实存在的会话——用户要的是它们归到「未归属的会话」，不是从列表里消失、
        更不是打不开。所以"哪些工作区有数据要读"与"哪些工作区摆在界面上可选"是两个问题，
        这里就是前者的答案。
        """
        visible = self.workspaces()
        seen = {ws.id for ws in visible}
        hidden = [
            ws for ws in self.registry.list(include_hidden=True) if ws.id not in seen
        ]
        return [*visible, *hidden]

    def list(self) -> list[dict[str, Any]]:
        return [self.describe(ws) for ws in self.workspaces()]

    def resolve(self, selection: str | None) -> Workspace:
        """把请求里的工作区选择解析成记录。**不接受省略。**"""
        text = (selection or "").strip()
        if not text:
            raise WorkspaceRequired(
                "新建会话必须指定 workspace（进程的绑定工作区只是预选项，不是默认值）"
            )
        # 绑定值可能没登记（我们不在启动时写盘），所以要显式认它——按 id 或按路径。
        for workspace in self.workspaces():
            if workspace.id == text or workspace.root == self._as_root(text):
                return workspace
        try:
            found = self.registry.find(text)
        except WorkspaceError as exc:  # 注册表损坏：读已降级为空表
            raise WorkspaceInvalid(str(exc)) from exc
        if found is None:
            raise WorkspaceMissing(
                f"没有这个工作区：{text}（用 GET /api/workspaces 看可选值）"
            )
        return found

    @staticmethod
    def _as_root(text: str) -> str:
        try:
            return str(Path(text).expanduser().resolve())
        except (OSError, RuntimeError):
            return text

    def find_known(self, selection: str) -> Workspace | None:
        """按 id 或路径找**已知**工作区：进程绑定的那个也算（它就在候选列表里）。"""
        text = (selection or "").strip()
        if not text:
            return None
        resolved = self._as_root(text)
        for workspace in self.workspaces():
            if workspace.id == text or workspace.root == resolved:
                return workspace
        return None

    def register(
        self, path: str, *, name: str | None = None, permission: str | None = None
    ) -> tuple[Workspace, bool]:
        """登记一个工作区，返回 ``(记录, 是否新建)``。

        数据层 ``registry.add`` 是幂等的（同一个目录永远同一个 id）；"是否重复"这件事
        在这里判断并交给传输层表达成 409——重复登记不该悄悄成功，也不该真的加第二遍。
        已登记的与**进程绑定但未登记**的都算重复：它已经出现在候选列表里了。
        """
        existing = self.find_known(path)
        if existing is not None:
            # 已经在列表里（进程绑定的或已登记的）：不重复添加，也不悄悄改名字/权限。
            return existing, False
        try:
            created = self.registry.add(path, name=name, permission=permission)
        except WorkspaceError as exc:
            raise WorkspaceInvalid(str(exc)) from exc
        except ValueError as exc:  # 未知权限模式
            raise WorkspaceInvalid(str(exc)) from exc
        return created, existing is None

    def require_new(
        self,
        path: str,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> Workspace:
        """``register`` 的严格版：已存在就 409，且带上已存在的那个（界面据此切过去）。

        name / permission 走**这一次** register：以前路由先 ``require_new(path)``
        写一次、再 ``registry.add(..., permission=...)`` 写第二次，于是非法模式
        会先落盘再失败（500 + 工作区已登记，重试变 409）。
        """
        workspace, created = self.register(path, name=name, permission=permission)
        if not created:
            raise WorkspaceExists(
                f"这个文件夹已经在工作区列表里：{workspace.name}（{workspace.root}）",
                detail={
                    "id": workspace.id,
                    "name": workspace.name,
                    "root": workspace.root,
                },
            )
        return workspace

    # ---------------- 系统文件夹选择器 ----------------

    def unregister(self, selection: str) -> Workspace:
        """从候选列表里摘掉一个工作区（界面的删除按钮 / ``avid workspace remove``）。

        **只摘索引，不动会话数据**：磁盘上的 ``<root>/.avid/sessions`` 一行不改，因此它
        下面的会话仍然列举得到、打得开（`known_workspaces()` 仍包含这条墓碑），只是不再
        出现在候选里——界面上它们归到「未归属的会话」。重新登记同一个目录即撤销。

        绑定工作地点（``default``）一律拒：它不进注册表，候选列表是"绑定值 + 注册表"，
        删掉注册表里的同 id 条目之后它照样排在第一个——假装删成功比拒绝更糟。
        """
        workspace = self.find_known(selection)
        if workspace is None:
            # 候选里没有：可能是**已删除过**的那条墓碑（`registry.get` 含 hidden），
            # 用它可以给出准确的错误，并且让"重复删"保持幂等。
            try:
                workspace = self.registry.get(selection)
            except WorkspaceError as exc:
                raise WorkspaceMissing(
                    f"没有这个工作区：{selection}（用 GET /api/workspaces 看可选值）"
                ) from exc
        if self.default is not None and workspace.id == self.default.id:
            raise WorkspaceBound(
                f"「{workspace.name}」是进程绑定的工作地点（{workspace.root}），"
                "它永远在候选列表里，不能从列表里删掉"
            )
        if workspace.hidden:
            # 已经是墓碑：重复删是幂等的（界面连点两下不该报错，也不该再写一次盘）。
            return workspace
        try:
            return self.registry.remove(workspace.id)
        except WorkspaceError as exc:
            raise WorkspaceInvalid(str(exc)) from exc

    def pick(self) -> str | None:
        """弹一次系统文件夹选择器，返回绝对路径；用户取消返回 ``None``。

        只应在回环地址上暴露：它等于"让服务进程在宿主机桌面上弹窗"。
        """
        if not self._pick_lock.acquire(blocking=False):
            raise PickerBusy("已经有一个文件夹选择器开着了；先去那边选完或取消")
        try:
            chosen = pick_directory()
        except PickerUnavailable as exc:
            raise PickerUnavailableError(str(exc)) from exc
        except PickerFailed as exc:
            raise PickerFailedError(str(exc)) from exc
        except PickerError as exc:  # 兜底：子类漏了就按失败处理
            raise PickerFailedError(str(exc)) from exc
        finally:
            self._pick_lock.release()

        if chosen is None:
            return None
        path = Path(chosen).expanduser()
        if not path.is_dir():
            raise WorkspaceInvalid(f"选中的路径不存在或不是目录：{chosen}")
        return str(path.resolve())

    def describe(self, workspace: Workspace) -> dict[str, Any]:
        record = workspace.to_dict()
        # `hidden` 是注册表内部的状态（墓碑），不属于线路格式：候选列表里根本没有墓碑。
        record.pop("hidden", None)
        record["is_default"] = self.default is not None and workspace.id == self.default.id
        return record

    # ---------------- 仓库 ----------------

    def sessions_root(self, workspace: Workspace) -> Path:
        if (
            self.default is not None
            and workspace.id == self.default.id
            and self.default_sessions_root is not None
        ):
            return self.default_sessions_root
        return sessions_root(workspace)

    def repo_for(self, workspace: Workspace) -> JsonlSessionRepo:
        repo = self._repos.get(workspace.id)
        if repo is None:
            repo = JsonlSessionRepo(
                self.sessions_root(workspace), workspace=workspace.id
            )
            self._repos[workspace.id] = repo
        return repo

    def repo_of_session(self, session_id: str) -> tuple[Workspace, SessionMetadata]:
        """会话属于哪个工作区。会话 id 不携带工作区信息，所以只能逐个库找。

        扫一次就把**所有**会话都记进缓存（反正已经列过了），命中缓存则完全不扫。
        与列举同一口径：**含墓碑**——删掉的工作区里的会话必须还打得开。
        """
        cached = self._lookup.get(session_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1], cached[2]

        expires = time.monotonic() + SESSION_LOOKUP_TTL_SECONDS
        for workspace in self.known_workspaces():
            for meta in self.repo_for(workspace).list():
                self._lookup[meta.id] = (expires, workspace, meta)

        cached = self._lookup.get(session_id)
        if cached is not None:
            return cached[1], cached[2]
        raise SessionNotFound(f"没有这个会话：{session_id}")

    def remember_session(self, workspace: Workspace, metadata: SessionMetadata) -> None:
        """新建会话时直接登记归属：不必等下一次全库扫描。"""
        self._lookup[metadata.id] = (
            time.monotonic() + SESSION_LOOKUP_TTL_SECONDS,
            workspace,
            metadata,
        )

    def forget_session(self, session_id: str) -> None:
        """删除/改名之后清掉：让下一次定位重新扫（删除过的会话不该再被缓存命中）。"""
        self._lookup.pop(session_id, None)

    def find_session(self, session_id: str) -> tuple[Workspace, SessionMetadata] | None:
        try:
            return self.repo_of_session(session_id)
        except SessionNotFound:
            return None

    @staticmethod
    def _find(repo: JsonlSessionRepo, session_id: str) -> SessionMetadata | None:
        for meta in repo.list():
            if meta.id == session_id:
                return meta
        return None

    def close(self) -> None:
        for repo in self._repos.values():
            repo.close()
        self._repos.clear()
        self._lookup.clear()


def bound_workspace(root: str | Path) -> Workspace:
    """进程绑定但不登记的工作地点。目录必须存在——否则早点报错，别等到写文件时。

    id 由根目录派生，所以它与"以后用 `avid workspace add` 登记同一个目录"得到的是
    同一个 id：绑定值与注册表项天然是同一份真相，不会出现两个工作区。
    """
    path = Path(root).expanduser()
    if not path.exists():
        raise WorkspaceInvalid(f"工作区目录不存在：{path}")
    if not path.is_dir():
        raise WorkspaceInvalid(f"不是目录：{path}")
    resolved = path.resolve()
    return Workspace(
        id=derive_id(resolved),
        root=str(resolved),
        name=resolved.name or "workspace",
        created_at=0,
        last_used_at=0,
    )


def single_workspace(root: str | Path) -> Workspace:
    """``Services(root=...)`` 专用：它传的的是**会话库路径**，工作地点由形状推出来。

    兼容两种形状：``<工作区>/.avid/sessions``（正常装配）与任意目录（测试直接给一个
    临时目录当会话库）。id 仍由工作区根派生，于是它和注册表里的同一目录一致。
    """
    from ..workspaces import derive_id

    sessions = Path(root).resolve()
    parts = sessions.parts
    if len(parts) >= 3 and parts[-2:] == (".avid", "sessions"):
        base = sessions.parent.parent
    else:
        base = sessions
    return Workspace(
        id=derive_id(base),
        root=str(base),
        name=base.name or "workspace",
        created_at=0,
        last_used_at=0,
    )


__all__ = [
    "bound_workspace",
    "WorkspaceInvalid",
    "WorkspaceMissing",
    "WorkspaceRequired",
    "WorkspaceService",
    "single_workspace",
]
