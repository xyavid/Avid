"""应用服务层（F1）：内核的第二个调用方。

与 ``cli.py`` 平级：``cli.py`` 把内核接到 stdin/stdout，这里把内核接到 HTTP。
两者都不改内核的调度（不变量：内核不知道有几个调用方）。

装配顺序有意义：**一个** ``JsonlSessionRepo`` 同时给运行注册表与会话读视图，
否则「运行中刷新页面」会因为同一个会话被两次 open 而失败。本模块不 import
FastAPI（A4），也不 import ``web/``；能力的输出（工具名、技能目录、模型名）
在这里汇总，``web/routes/meta.py`` 只负责加构建戳与线格式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ai.config import ConfigError, load_config
from ..policy.skills import SkillLoader
from ..runtime.events import EVENT_TYPES, now_ms
from ..session import JsonlSessionRepo
from ..tools import TOOLS
from ..tools import workspace
from ..workspaces import Workspace, WorkspaceRegistry
from .approvals import APPROVAL_TIMEOUT_SECONDS
from .runs import REPLAY_BUFFER_SIZE, RunRegistry
from .sessions import SessionService
from .tasks import TaskService
from .workspaces import WorkspaceService, bound_workspace, single_workspace

# 破坏性变更时 +1。客户端只在**不兼容**时失败收敛；加可选事件不改它（§6.3）。
API_VERSION = 1

# 特性表：客户端按特性分支，不按版本号分支。声明的是**实际可用**的能力。
FEATURES: dict[str, int] = {
    "approvals": 1,
    "cancel": 1,
    "tasks": 1,
    "sessions": 1,
    "entries": 1,
    "deltas": 1,  # F3：内核按 SSE 流式解析，delta 经事件流投递（需 ?deltas=1 订阅）
    "branches": 1,  # F4：分支列表 / 分叉 / 在指定分支上运行
    "workspaces": 1,  # 阶段 18：工作区注册表 + 按工作区建会话
    "permission_modes": 1,  # 阶段 18：POST /runs 接受 permission（strict/workspace/system）
}

# 事件流相关常量对客户端可见：它据此设超时与对账阈值（I13）。
STREAM_HEARTBEAT_SECONDS = 15.0
TERMINAL_FALLBACK_SECONDS = 30.0


class Services:
    """一组进程内服务。一个 Web 应用装配一份。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        workspace_root: str | Path | None = None,
        chat: Any = None,
        tool_registry: Any = None,
        buffer_size: int = REPLAY_BUFFER_SIZE,
        approval_timeout: float = APPROVAL_TIMEOUT_SECONDS,
        registry: WorkspaceRegistry | None = None,
    ) -> None:
        """任何装配都先绑定一个**工作地点**，但**不写盘**（注册表只由用户显式动作写入）。

        * ``root``：直接给会话库路径（测试用）；工作地点由路径形状推出来。
        * ``workspace_root``：给工作区根（`avid web --workspace`），目录必须存在。
        * 都不给：取进程默认根（正常就是 cwd）——**没有"没有工作地点"的进程**。

        绑定值可能是**未登记**的：它照常出现在 `GET /api/workspaces`（`is_default=true`）、
        也照常可被 `POST /api/sessions` 解析（id 由根目录派生）。启动写盘会让"看一眼注册表"
        与"起过服务"变成不可区分的两件事，而用户只想知道自己登记过哪些。
        """
        self.registry = registry or WorkspaceRegistry()
        if root is not None:
            default = single_workspace(root)
            sessions_root: Path | None = Path(root)
        elif workspace_root is not None:
            default = bound_workspace(workspace_root)
            sessions_root = None  # 用 `<root>/.avid/sessions`
        else:
            default = bound_workspace(workspace.WORKSPACE_ROOT)
            sessions_root = None
        self.workspaces = WorkspaceService(
            self.registry, default=default, default_sessions_root=sessions_root
        )
        self.root = (
            self.workspaces.sessions_root(default) if default is not None else None
        )
        self.runs = RunRegistry(
            self.workspaces,
            chat=chat,
            tool_registry=tool_registry,
            buffer_size=buffer_size,
            approval_timeout=approval_timeout,
        )
        self.sessions = SessionService(self.workspaces, self.runs)
        self.tasks = TaskService()
        self.started_at = now_ms()

    # ---------------- 兼容访问器 ----------------

    @property
    def repo(self) -> JsonlSessionRepo:
        """进程绑定的工作地点的会话仓库（旧访问点；新代码请用 `workspaces.repo_for`）。

        多工作区模式没有"唯一仓库"这种东西，所以显式报错而不是随便挑一个——
        "挑错了库"正是阶段 18 要消灭的那类静默错误。
        """
        if self.workspaces.default is None:
            raise RuntimeError(
                "多工作区模式没有单一会话仓库；"
                "请用 services.workspaces.repo_for(workspace)"
            )
        return self.workspaces.repo_for(self.workspaces.default)

    # ---------------- 能力面 ----------------

    def meta(self) -> dict[str, Any]:
        """版本、特性表、能力面。**不含**构建戳（那是 web/ 的静态资源事实）。"""
        return {
            "api_version": API_VERSION,
            "features": dict(FEATURES),
            "event_types": list(EVENT_TYPES),
            "capabilities": {
                "tools": [item["function"]["name"] for item in TOOLS],
                "skills": self.skills(),
                "model": self.model_name(),
                # 进程绑定的工作地点根；它总是存在（`Services` 必绑定一个），
                # 所以这里只是给界面的文本提示，**候选列表**一律走
                # GET /api/workspaces，避免同一概念两种拼写。
                "workspace": (
                    self.workspaces.default.root
                    if self.workspaces.default is not None
                    else str(workspace.WORKSPACE_ROOT)
                ),
            },
            "stream": {
                "heartbeat_seconds": STREAM_HEARTBEAT_SECONDS,
                "terminal_fallback_seconds": TERMINAL_FALLBACK_SECONDS,
                "replay_buffer_size": self.runs.buffer_size,
            },
        }

    def skills(self) -> list[dict[str, str]]:
        """技能目录：name + 一行描述，与 system prompt 同源（同一个 SkillLoader）。"""
        loader = SkillLoader().scan()
        return [
            {"name": name, "description": loader.skills[name]["description"]}
            for name in sorted(loader.skills)
        ]

    @staticmethod
    def model_name() -> str | None:
        """没配模型也要能打开界面——返回 None，让前端提示去配 .env。"""
        try:
            return load_config().model
        except ConfigError:
            return None

    def close(self) -> None:
        self.workspaces.close()


__all__ = [
    "API_VERSION",
    "FEATURES",
    "STREAM_HEARTBEAT_SECONDS",
    "TERMINAL_FALLBACK_SECONDS",
    "Services",
]
