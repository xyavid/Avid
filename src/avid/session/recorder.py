"""messages → 会话条目：把 agent_loop 的观察点接上会话。

``agent_loop`` 只发通知、不认识会话（不变量 I7）；把通知变成条目是这里的事。
一次 ``append_message`` 就是一次提交，于是"每条结算消息一条记录"这件事与
循环的进度天然对齐——崩在任意一条之后，之前的内容都已经在文件里。

会话名与分支名由调用方决定：本模块只负责"消息进、条目出"。
"""

from __future__ import annotations

import logging
from typing import Any

from .session import SessionBranch, StorageBackedSession

logger = logging.getLogger("avid.session.recorder")

DEFAULT_BRANCH = "main"


class SessionRecorder:
    """把 ``agent_loop(on_message=recorder.on_message)`` 的消息逐条落库。"""

    def __init__(
        self, session: StorageBackedSession, branch: str = DEFAULT_BRANCH
    ) -> None:
        self.session = session
        self.branch = branch
        self.entry_ids: list[str] = []
        self._branch: SessionBranch | None = None

    def ensure_branch(self) -> SessionBranch:
        """``create`` 不隐式建分支，所以第一次落库之前显式建一次。"""
        if self._branch is None:
            found = self.session.branch(self.branch)
            self._branch = found if found is not None else self.session.create_branch(
                self.branch, None
            )
        return self._branch

    def on_message(self, message: dict[str, Any]) -> str:
        """与 ``agent_loop`` 的观察点同签名，可直接作为参数传入。

        返回条目 id：调用方（``svc/runs.py``）要把它带进 durable 事件，
        前端因此不必自己维护会话身份（设计文档 §5.3）。
        """
        branch = self.ensure_branch()
        entry_id = branch.append_message(message)
        self.entry_ids.append(entry_id)
        logger.debug("会话落库 %s：%s", entry_id, message.get("role"))
        return entry_id

    @property
    def count(self) -> int:
        return len(self.entry_ids)
