"""工作区：一个本地目录，同时承担三件事——干活的地点、权限边界、会话归属。

**注册表不是权威。** 会话数据始终在各自工作区的 ``<root>/.avid/sessions/`` 下，
工作区 id 由根目录派生，所以删掉注册表不丢任何会话，重复登记同一个目录也是幂等的。
注册表只回答一个问题：**这台机器上有哪些工作区可选**（``avid workspace list``、
Web 的新建会话选择器）。

id 由根目录派生（``w-`` + sha1(绝对路径)[:12]）而不是随机分配，正是为了让
"注册表丢失"与"重复登记"这两件事都不产生第二份真相。

文件放在用户级 ``~/.avid/workspaces.json``（``AVID_HOME`` 可覆盖，测试用它隔离），
因为"列出候选工作区"这个动作必须能在一个工作区之外发生。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .policy.permission import DEFAULT_MODE, validate_mode
from .runtime.events import now_ms

logger = logging.getLogger("avid.workspaces")

AVID_HOME_ENV = "AVID_HOME"
REGISTRY_FILE = "workspaces.json"
REGISTRY_VERSION = 1
SESSION_DIR = ".avid/sessions"

# 常用占位名，避免 root 是文件系统根时显示成空串。
_UNNAMED = "workspace"


class WorkspaceError(Exception):
    """工作区操作失败。消息面向使用者，直接可读。"""


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceRegistryCorrupt(WorkspaceError):
    pass


def home_dir() -> Path:
    """用户级 Avid 目录：``AVID_HOME`` 优先，否则 ``~/.avid``。"""
    override = os.environ.get(AVID_HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".avid"


def registry_path() -> Path:
    return home_dir() / REGISTRY_FILE


def derive_id(root: str | Path) -> str:
    """由根目录派生稳定 id：同一目录永远得到同一个 id。"""
    resolved = str(Path(root).expanduser().resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:12]
    return f"w-{digest}"


@dataclass(frozen=True)
class Workspace:
    id: str
    root: str
    name: str
    created_at: int
    last_used_at: int
    default_permission: str = DEFAULT_MODE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root": self.root,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "default_permission": self.default_permission,
        }


def _parse(raw: Any) -> Workspace | None:
    if not isinstance(raw, dict):
        return None
    root = raw.get("root")
    if not isinstance(root, str) or not root.strip():
        return None
    created = raw.get("created_at")
    used = raw.get("last_used_at")
    mode = raw.get("default_permission", DEFAULT_MODE)
    try:
        permission = validate_mode(mode)
    except ValueError:
        logger.warning("工作区 %s 的默认权限 %r 不认识，按默认处理", root, mode)
        permission = DEFAULT_MODE
    return Workspace(
        id=str(raw.get("id") or derive_id(root)),
        root=str(Path(root).expanduser().resolve()),
        name=str(raw.get("name") or Path(root).name or _UNNAMED),
        created_at=int(created) if isinstance(created, int) else now_ms(),
        last_used_at=int(used) if isinstance(used, int) else now_ms(),
        default_permission=permission,
    )


class WorkspaceRegistry:
    """已知工作区的索引。读宽容、写拒绝——损坏的文件不会被静默覆盖。"""

    def __init__(self, path: str | Path | None = None, *, now: Callable[[], int] = now_ms):
        self.path = Path(path) if path is not None else registry_path()
        self._now = now

    # ---------------- 读写 ----------------

    def _read(self) -> list[Workspace]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceRegistryCorrupt(
                f"工作区注册表读不了：{self.path}（{exc}）。"
                "修好这个文件，或删掉它再重新登记工作区。"
            ) from exc
        if not isinstance(raw, dict):
            raise WorkspaceRegistryCorrupt(
                f"工作区注册表格式不对：{self.path}。删掉它再重新登记工作区。"
            )
        items = raw.get("workspaces")
        if not isinstance(items, list):
            return []
        return [ws for ws in (_parse(item) for item in items) if ws is not None]

    def _write(self, workspaces: Iterable[Workspace]) -> None:
        payload = {
            "version": REGISTRY_VERSION,
            "workspaces": [ws.to_dict() for ws in workspaces],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temp, self.path)

    def list(self) -> list[Workspace]:
        """按最近使用倒序。文件损坏时**不阻断运行**：返回空表并留一条日志。"""
        try:
            items = self._read()
        except WorkspaceRegistryCorrupt as exc:
            logger.warning("%s", exc)
            return []
        return sorted(items, key=lambda ws: (-ws.last_used_at, ws.id))

    # ---------------- 查询 ----------------

    def find(self, selection: str | None) -> Workspace | None:
        """按 id 或路径查；``None``/空串返回 None。"""
        if not selection:
            return None
        text = str(selection).strip()
        if not text:
            return None
        try:
            items = self._read()
        except WorkspaceRegistryCorrupt as exc:
            logger.warning("%s", exc)
            items = []

        for ws in items:
            if ws.id == text:
                return ws

        candidate = str(Path(text).expanduser().resolve())
        for ws in items:
            if ws.root == candidate:
                return ws
        return None

    def get(self, selection: str) -> Workspace:
        found = self.find(selection)
        if found is None:
            raise WorkspaceNotFound(
                f"没有这个工作区：{selection}。用 `avid workspace list` 看已登记的工作区，"
                "或 `avid workspace add <路径>` 登记一个。"
            )
        return found

    # ---------------- 写 ----------------

    def add(
        self,
        root: str | Path,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> Workspace:
        """登记一个目录。同一个目录重复登记是幂等的（id 由路径派生）。"""
        path = Path(root).expanduser()
        if not path.exists():
            raise WorkspaceError(f"目录不存在：{path}")
        if not path.is_dir():
            raise WorkspaceError(f"不是目录：{path}")
        resolved = path.resolve()
        mode = validate_mode(permission or DEFAULT_MODE)

        items = self._read()  # 损坏时抛错：不覆盖可能是好的数据
        stamp = self._now()
        for index, ws in enumerate(items):
            if ws.id == derive_id(resolved):
                updated = replace(
                    ws,
                    name=name or ws.name,
                    last_used_at=stamp,
                    default_permission=permission or ws.default_permission,
                )
                items[index] = updated
                self._write(items)
                return updated

        created = Workspace(
            id=derive_id(resolved),
            root=str(resolved),
            name=name or resolved.name or _UNNAMED,
            created_at=stamp,
            last_used_at=stamp,
            default_permission=mode,
        )
        self._write([*items, created])
        return created

    def set_permission(self, selection: str, permission: str) -> Workspace:
        mode = validate_mode(permission)
        found = self.get(selection)
        updated = self._update(found.id, default_permission=mode)
        assert updated is not None
        return updated

    def remove(self, selection: str) -> Workspace:
        """只从索引里摘掉，**不动磁盘上的任何会话数据**。"""
        found = self.get(selection)
        items = [ws for ws in self._read() if ws.id != found.id]
        self._write(items)
        return found

    def _update(self, workspace_id: str, **changes: Any) -> Workspace | None:
        items = self._read()
        for index, ws in enumerate(items):
            if ws.id == workspace_id:
                items[index] = replace(ws, **changes)
                self._write(items)
                return items[index]
        return None

    # 会话库路径见模块函数 `sessions_root()`（不读实例状态，不做 staticmethod）。


def sessions_root(workspace: Workspace | str) -> Path:
    """一个工作区的会话库固定落在它自己的 ``.avid/sessions/`` 下。

    模块函数而不是 `WorkspaceRegistry` 的 staticmethod（P3-4）：它不读实例状态，
    挂在类上只会让人以为"要先有个注册表才能算这个路径"。
    """
    root = workspace.root if isinstance(workspace, Workspace) else str(workspace)
    return Path(root) / SESSION_DIR
