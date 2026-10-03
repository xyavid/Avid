"""Application services: one assembly of the run registry, session and workspace services."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from ..ai.byok import byok_model_candidates, resolve_chat
from ..ai.config import KNOWN_MODELS, ConfigError
from ..policy.sandbox import default_backend_summary
from ..policy.skills import SkillLoader, default_skills_dir
from ..runtime.events import (
    EVENT_TYPES,
    STREAM_HEARTBEAT_SECONDS,
    TERMINAL_FALLBACK_SECONDS,
    now_ms,
)
from ..session import JsonlSessionRepo
from ..tools import TOOLS, workspace
from ..workspaces import WorkspaceRegistry
from .approvals import APPROVAL_TIMEOUT_SECONDS
from .errors import TooManyStreams
from .picker import available_backend
from .runs import (
    MAX_EVENT_BUFFER,
    MAX_RETAINED_RUNS,
    REPLAY_BUFFER_SIZE,
    TERMINAL_RETENTION_SECONDS,
    RunRegistry,
)
from .sessions import SessionService
from .workspaces import WorkspaceService, bound_workspace, single_workspace

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
    # The run endpoint accepts a permission mode: manual, auto or full.
    "permission_modes": 1,
    # Orthogonal permission axes, the four-level deny ladder and the sandbox.
    "security_layers": 1,
    # Full access needs an explicit acknowledgement and is never the default.
    "full_access": 1,
    # Creating a workspace can open a host folder picker.
    "workspace_picker": 1,
    # Deleting a workspace only drops its registry entry; its sessions stay readable.
    "workspace_delete": 1,
    # Branches carry a usage snapshot reported in one shared schema.
    "usage": 1,
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
            default = single_workspace(root)
            sessions_root: Path | None = Path(root)
        elif workspace_root is not None:
            default = bound_workspace(workspace_root)
            sessions_root = None  # None means the store is derived as <root>/.avid/sessions.
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
            retention_seconds=retention_seconds,
            max_runs=max_runs,
            max_events=max_events,
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
                # 可切换的候选（本次运行的模型覆盖用）；空内核配置下也能列出来。
                "known_models": list(KNOWN_MODELS),
                # BYOK 候选（providerId/modelId ref + 展示名）；没有 BYOK 配置时为空，
                # 界面回落 known_models。
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
        """Per-run model picker candidates: BYOK refs first; empty when unconfigured.

        未配置任何 BYOK 提供商时返回空列表，界面回落内核窗口表的 known_models。
        """
        try:
            return byok_model_candidates()
        except ConfigError:
            # 配置文件坏了也要让界面能加载——错误文案会在设置面板里暴露。
            return []

    def close(self) -> None:
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
