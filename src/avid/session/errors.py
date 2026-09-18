"""会话层的错误分类。

每一类都指向**一个不同的下一步动作**——阶段 3 的教训是：只说"失败"会让调用方
（以及模型）无从判断该重试、该换做法还是该停下来。

* 仓库层（``NotFound`` / ``Exists`` / ``AlreadyOpen``）：改调用方式，数据没坏
* 句柄层（``Closed`` / ``Busy``）：换一个句柄，或者等当前变更结束
* 数据层（``Invariant`` / ``Storage``）：不要自动重试，需要人看
"""

from __future__ import annotations

from typing import Any


class SessionError(Exception):
    """会话层一切可预期失败的基类。CLI 只需捕获这一个名字。"""


class SessionNotFoundError(SessionError):
    """按 id 找不到会话。删除之后再 open/delete 会看到它。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"没有这个会话：{session_id}。用 --list-sessions 看看有哪些。")


class SessionExistsError(SessionError):
    """要用的 id 已被占用。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"会话 id 已存在：{session_id}。换一个 id，或直接续接它。")


class SessionAlreadyOpenError(SessionError):
    """同一会话已经被一个句柄占着（同一 id 只允许一个打开中的句柄）。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"会话已经打开：{session_id}。先关掉现有句柄再打开。")


class SessionClosedError(SessionError):
    """句柄（或仓库、存储）已经关闭，之后不再接受任何操作。"""


class SessionBusyError(SessionError):
    """同一个线程在变更回调里又发起变更或关闭——同步语义下这会自锁，所以直接报错。"""


class SessionLockedError(SessionError):
    """会话文件被**另一个进程**占着（跨进程互斥）。

    同一 id 同一时刻只允许一个写入者：两个进程各自用内存里的 ``next_seq`` 追加，
    会写出重复的 ``seq``，而重放校验拒绝非单调 ``seq``——那会让整个会话文件此后
    不可读。所以拿不到锁时直接报错，而不是并发写。
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            f"会话被另一个进程占用（{label}）：同一会话同一时刻只允许一个进程写入。"
            "等那个进程结束再试，不要用两个进程同时写同一个会话。"
        )


class SessionInvariantError(SessionError):
    """持久化状态自相矛盾（缺 parent、重复 id、未知分支），不能继续推进。"""


class SessionStorageError(SessionError):
    """存储不可用或格式非法：文件损坏、版本不认识、读写失败。"""


class SessionInvalidIdError(SessionError):
    """会话 id 非法（空白或含路径分隔符），不能安全地变成文件名。"""

    def __init__(self, session_id: Any, reason: str) -> None:
        super().__init__(f"会话 id 非法：{session_id!r}（{reason}）")
        self.session_id = session_id
        self.reason = reason


class SessionInvalidBranchError(SessionError):
    """分支名非法。"""

    def __init__(self, branch: str, reason: str) -> None:
        super().__init__(f"分支名非法：{branch!r}（{reason}）")
        self.branch = branch
        self.reason = reason


class SessionBranchExistsError(SessionError):
    """分支已存在。分支头一旦建立就不再重建——否则会悄悄丢掉一条链。"""

    def __init__(self, branch: str) -> None:
        super().__init__(f"分支已存在：{branch}。用 branch() 取它，不要重复创建。")


class SessionUnknownTargetError(SessionError):
    """分支的起点条目不存在。"""

    def __init__(self, target_id: str) -> None:
        super().__init__(f"找不到条目：{target_id}。分支起点必须是已经存在的条目。")


class SessionInvalidMessageError(SessionError):
    """消息不是一条可以落库的记录。在写入前拦住，避免污染会话文件。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"消息无法写入会话：{reason}")
