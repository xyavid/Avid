"""messages → 会话条目：把 agent_loop 的观察点接上会话。

``agent_loop`` 只发通知、不认识会话（不变量 I7）；把通知变成条目是这里的事。
一次 ``append_message`` 就是一次提交，于是"每条结算消息一条记录"这件事与
循环的进度天然对齐——崩在任意一条之后，之前的内容都已经在文件里。

会话名与分支名由调用方决定：本模块只负责"消息进、条目出"（外加运行用量台账的
一次值写入，见 :meth:`SessionRecorder.record_usage`）。
"""

from __future__ import annotations

import logging
from typing import Any

from .session import SessionBranch, StorageBackedSession
from .types import MESSAGE_ENTRY, NOTICE_ENTRY
from .values import DEFAULT_BRANCH, branch_usage

logger = logging.getLogger("avid.session.recorder")


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

    def on_message(self, message: dict[str, Any], *, notice: bool = False) -> str:
        """与 ``agent_loop`` 的观察点同签名，可直接作为参数传入。

        返回条目 id：调用方（``svc/runs.py``）要把它带进 durable 事件，
        前端因此不必自己维护会话身份（设计文档 §5.3）。

        ``notice=True`` 用于内核注入的提醒（TODO 提醒 / Stop nudge）：它们照样落库
        （续接时模型看到的历史必须与当时逐字一致），但类型不同，渲染侧据此不当成
        用户说的话——nudge 的文本由 Stop hook 任意给定，文本上认不出来。
        """
        branch = self.ensure_branch()
        entry_id = branch.append_message(
            message, entry_type=NOTICE_ENTRY if notice else MESSAGE_ENTRY
        )
        self.entry_ids.append(entry_id)
        logger.debug("会话落库 %s：%s", entry_id, message.get("role"))
        return entry_id

    @property
    def count(self) -> int:
        return len(self.entry_ids)

    def record_usage(self, payload: dict[str, Any]) -> None:
        """把一次运行的用量快照写进会话值（按分支，覆盖式）。

        为什么不落成条目：用量不是模型看到的历史，而 ``messages_for_branch`` 只投影
        ``message`` / ``notice`` 两类条目——落成条目要么污染历史，要么得再加一条
        "哪些类型不算历史"的例外。值机制本来就装"必须持久化但不是消息"的东西，
        形状也不动存储格式（``STORAGE_VERSION`` 不变）。

        覆盖式：一个分支只保留最近一次运行的读数，正是"进入会话时看当前占用"要的。
        """
        self.session.set_value(branch_usage(self.branch), payload)
        logger.debug("会话落库 usage：分支 %s", self.branch)
