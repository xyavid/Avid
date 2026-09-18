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
from .approvals import APPROVAL_TIMEOUT_SECONDS
from .runs import REPLAY_BUFFER_SIZE, SESSION_DIR, RunRegistry
from .sessions import SessionService
from .tasks import TaskService

# 破坏性变更时 +1。客户端只在**不兼容**时失败收敛；加可选事件不改它（§6.3）。
API_VERSION = 1

# 特性表：客户端按特性分支，不按版本号分支。声明的是**实际可用**的能力。
FEATURES: dict[str, int] = {
    "approvals": 1,
    "cancel": 1,
    "tasks": 1,
    "sessions": 1,
    "entries": 1,
    "deltas": 0,  # F3 未实施：内核仍非流式，故不投递 delta
    "branches": 0,  # 前端侧分支视图是 F4
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
        chat: Any = None,
        tool_registry: Any = None,
        buffer_size: int = REPLAY_BUFFER_SIZE,
        approval_timeout: float = APPROVAL_TIMEOUT_SECONDS,
    ) -> None:
        self.root = Path(root) if root is not None else Path(workspace.WORKSPACE_ROOT) / SESSION_DIR
        self.repo = JsonlSessionRepo(self.root)
        self.runs = RunRegistry(
            self.repo,
            chat=chat,
            tool_registry=tool_registry,
            buffer_size=buffer_size,
            approval_timeout=approval_timeout,
        )
        self.sessions = SessionService(self.repo, self.runs)
        self.tasks = TaskService()
        self.started_at = now_ms()

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
                "workspace": str(workspace.WORKSPACE_ROOT),
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
        self.repo.close()


__all__ = [
    "API_VERSION",
    "FEATURES",
    "STREAM_HEARTBEAT_SECONDS",
    "TERMINAL_FALLBACK_SECONDS",
    "Services",
]
