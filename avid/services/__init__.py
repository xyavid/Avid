"""Application services: one assembly of the run registry, session and workspace services."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..agent.events import (
    EVENT_TYPES,
    STREAM_HEARTBEAT_SECONDS,
    TERMINAL_FALLBACK_SECONDS,
    now_ms,
)
from ..agent.skills import SkillLoader, default_skills_dir
from ..agent.tools import TOOLS, workspace
from ..index import SessionIndexer
from ..index.indexer import workspace_lookup
from ..providers.byok import byok_model_candidates, resolve_chat
from ..providers.config import ConfigError
from ..security import userdirs
from ..security.sandbox import default_backend_summary
from ..session import JsonlSessionRepo
from .approvals import APPROVAL_TIMEOUT_SECONDS
from .errors import SessionBusy, TooManyStreams
from .picker import available_backend
from .runs import (
    MAX_EVENT_BUFFER,
    MAX_RETAINED_RUNS,
    REPLAY_BUFFER_SIZE,
    TERMINAL_RETENTION_SECONDS,
    RunRegistry,
)
from .sessions import SessionService
from .workspace_registry import WorkspaceRegistry
from .workspaces import WorkspaceService, bound_workspace, single_workspace

logger = logging.getLogger("avid.services")

# Bump on a breaking wire change; clients branch on the feature table, not on this number.
API_VERSION = 1

# Capability flags: 1 means the running build actually serves that feature.
FEATURES: dict[str, int] = {
    "approvals": 1,
    "cancel": 1,
    "sessions": 1,
    "entries": 1,
    # Deltas arrive on the event stream and require opting in when subscribing.
    "deltas": 1,
    "branches": 1,
    "workspaces": 1,
    # 权限体系轻量化（阶段 51）：默认直接跑，毁灭级命令双确认，无模式可选。
    "danger_confirm": 1,
    "security_layers": 1,
    # Full access needs an explicit acknowledgement and is never the default.
    "full_access": 1,
    # Creating a workspace can open a host folder picker.
    "workspace_picker": 1,
    # Deleting a workspace only drops its registry entry; its sessions stay readable.
    "workspace_delete": 1,
    # Branches carry a usage snapshot reported in one shared schema.
    "usage": 1,
    # 内容检索（阶段 57）：本地索引 + FTS5，端点 GET /api/search。
    "search": 1,
}

# Ceiling on concurrently open event streams; subscribers beyond it are rejected, not queued.
# Subscribers wait on the event loop instead of holding a worker thread, so this is a guard rail.
MAX_CONCURRENT_STREAMS = 256

# How long the skill listing is cached in process, since the meta endpoint is polled often.
# The window stays short because a changed skill directory should take effect soon after.
SKILLS_CACHE_SECONDS = 5.0


class StreamSlots:
    """Concurrency budget for open event streams; acquisition fails instead of queueing."""

    def __init__(self, limit: int = MAX_CONCURRENT_STREAMS) -> None:
        self.limit = limit
        # Counting happens under a lock because acquisition and release run on different threads.
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active:
                self._active -= 1


class Services:
    """The set of in-process services one transport application assembles."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        workspace_root: str | Path | None = None,
        chat: Any = None,
        tool_registry: Any = None,
        buffer_size: int = REPLAY_BUFFER_SIZE,
        approval_timeout: float = APPROVAL_TIMEOUT_SECONDS,
        retention_seconds: float = TERMINAL_RETENTION_SECONDS,
        max_runs: int = MAX_RETAINED_RUNS,
        max_events: int = MAX_EVENT_BUFFER,
        registry: WorkspaceRegistry | None = None,
    ) -> None:
        """Bind a working location for the process without writing to the registry.

        The bound value may stay unregistered, and it is still listed and still resolvable.
        """
        self.registry = registry or WorkspaceRegistry()
        if root is not None:
            # An explicit store path is used verbatim: the bound workspace keeps its sessions there.
            default = single_workspace(root)
            sessions_root: Path | None = Path(root)
        elif workspace_root is not None:
            default = bound_workspace(workspace_root)
            sessions_root = None  # None = the shared session directory plus this workspace's id.
        else:
            default = bound_workspace(workspace.WORKSPACE_ROOT)
            sessions_root = None
        self.workspaces = WorkspaceService(
            self.registry, default=default, default_sessions_root=sessions_root
        )
        # 会话索引：本进程写自己的会话、索引自己的会话；启动后先在后台补齐（运行不等它）。
        # root= 直传了具体会话库路径时只索引那一个目录——那是那个接缝的语义。
        def _roots() -> list[Path]:
            return [Path(sessions_root)] if sessions_root is not None else [userdirs.sessions_dir()]

        # 索引是可丢的派生层：建不起来（盘满/目录只读/库坏了）就整体停用，
        # 绝不能连累会话读写与运行——搜索会明确报「索引不可用」。
        try:
            self.indexer: SessionIndexer | None = SessionIndexer(
                roots=_roots,
                lookup_workspace=workspace_lookup(
                    lambda: (
                        (item.id, item.root, item.name)
                        for item in self.workspaces.known_workspaces()
                    )
                ),
            )
            self.indexer.start()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("索引不可用，本进程停用索引（会话不受影响）：%s", exc)
            self.indexer = None
        self.root = (
            self.workspaces.sessions_root(default) if default is not None else None
        )
        self.runs = RunRegistry(
            self.workspaces,
            chat=chat,
            tool_registry=tool_registry,
            buffer_size=buffer_size,
            approval_timeout=approval_timeout,
            retention_seconds=retention_seconds,
            max_runs=max_runs,
            max_events=max_events,
            indexer=self.indexer,
        )
        self.sessions = SessionService(self.workspaces, self.runs)
        # The stream budget is process-wide because it protects a process-wide resource.
        self.streams = StreamSlots()
        self.started_at = now_ms()
        # Cached skill listing and the monotonic time it was read, refreshed after the cache window.
        self._skills: list[dict[str, str]] | None = None
        self._skills_at = 0.0

    # Compatibility accessors for call sites that predate multi-workspace mode.

    @property
    def repo(self) -> JsonlSessionRepo:
        """Repository of the process-bound workspace; multi-workspace setups have no single one."""
        if self.workspaces.default is None:
            raise RuntimeError(
                "多工作区模式没有单一会话仓库；"
                "请用 services.workspaces.repo_for(workspace)"
            )
        return self.workspaces.repo_for(self.workspaces.default)

    # Capability surface reported to clients.

    def meta(self) -> dict[str, Any]:
        """Version, feature table and capability surface, without the build stamp of assets."""
        return {
            "api_version": API_VERSION,
            "features": dict(FEATURES),
            "event_types": list(EVENT_TYPES),
            "capabilities": {
                "tools": [item["function"]["name"] for item in TOOLS],
                "skills": self.skills(),
                "model": self.model_name(),
                # BYOK 候选（providerId/modelId ref + 展示名）；不预置任何模型选项，
                # 没配 BYOK 时就是空的——模型选项只来自用户自己的配置。
                "models": self.model_candidates(),
                # Root of the process-bound workspace; candidates come from the workspaces endpoint.
                "workspace": (
                    self.workspaces.default.root
                    if self.workspaces.default is not None
                    else str(workspace.WORKSPACE_ROOT)
                ),
                # Backend this machine would use for the folder picker, or None when there is none.
                "workspace_picker": available_backend(),
                # Sandbox backend probe: backend, availability, network policy and reason.
                "sandbox": default_backend_summary(),
            },
            # Event-stream thresholds published so clients match the server's timings.
            "stream": {
                "heartbeat_seconds": STREAM_HEARTBEAT_SECONDS,
                "terminal_fallback_seconds": TERMINAL_FALLBACK_SECONDS,
                "replay_buffer_size": self.runs.buffer_size,
            },
        }

    def skills(self) -> list[dict[str, str]]:
        """Skill names with a one-line description, from the loader that also builds the prompt."""
        now = time.monotonic()
        # Re-scan only after the short cache window so this read-only endpoint stays cheap.
        if self._skills is None or now - self._skills_at > SKILLS_CACHE_SECONDS:
            root = self.workspaces.default.root if self.workspaces.default else None
            loader = SkillLoader(default_skills_dir(root)).scan()
            self._skills = [
                {"name": name, "description": loader.skills[name]["description"]}
                for name in sorted(loader.skills)
            ]
            self._skills_at = now
        return list(self._skills)

    @staticmethod
    def model_name() -> str | None:
        """Configured model name, or None when unconfigured so the UI can still load."""
        try:
            return resolve_chat().model
        except ConfigError:
            return None

    @staticmethod
    def model_candidates() -> list[dict[str, str]]:
        """Per-run model picker candidates: BYOK refs only; empty when unconfigured."""
        try:
            return byok_model_candidates()
        except ConfigError:
            # 配置文件坏了也要让界面能加载——错误文案会在设置面板里暴露。
            return []

    def rebind_session_store(self) -> None:
        """Re-derive the repositories after the session directory moved.

        活动 run 期间**拒绝**：run 手里的会话句柄是从按工作区缓存的仓库里开的，而解绑会关掉
        那些仓库（连同句柄）。关掉之后这次运行的下一次提交抛 SessionClosed，模型那条回复
        就永远不落盘——丢的不是索引，是对话。所以先让它跑完再来改设置。
        """
        busy = self.runs.active_runs()
        if busy:
            raise SessionBusy(
                "还有运行在跑，先等它结束再改会话目录"
                f"（进行中：{'、'.join(busy)}）。这次运行写的是老位置，改到这里不影响它。"
            )
        self.workspaces.rebind()

    def close(self) -> None:
        if self.indexer is not None:
            self.indexer.stop()
        self.workspaces.close()


__all__ = [
    "API_VERSION",
    "FEATURES",
    "MAX_CONCURRENT_STREAMS",
    "STREAM_HEARTBEAT_SECONDS",
    "TERMINAL_FALLBACK_SECONDS",
    "Services",
    "StreamSlots",
    "TooManyStreams",
]
