"""会话 id 与时钟。

id 用 UUIDv7 形状：前 48 位是毫秒时间戳，其余随机。只用标准库——
``uuid.uuid7()`` 要到 Python 3.14 才有，而"id 时间有序"这点值得自己保留：
会话文件按时间命名、列表按时间排序、日志里一眼能看出先后。

时钟统一成**整数毫秒**，仓库、存储、id 生成器共用一个可注入的 ``now``，
测试里因此可以完全确定地构造文件内容与 id。
"""

from __future__ import annotations

import secrets
import time
import uuid

from .errors import SessionInvalidIdError

MAX_ID_LENGTH = 200


def now_ms() -> int:
    return int(time.time() * 1000)


def validate_session_id(session_id: object) -> str:
    """会话 id 能否安全地出现在文件名里。

    两个后端共用这一条校验——否则内存后端会接受一个文件后端拒绝的 id，
    一致性就只是句空话。
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise SessionInvalidIdError(session_id, "必须是非空字符串")
    if len(session_id) > MAX_ID_LENGTH:
        raise SessionInvalidIdError(session_id, f"长度不能超过 {MAX_ID_LENGTH}")
    for char in ("/", "\\", "\u0000"):
        if char in session_id:
            raise SessionInvalidIdError(session_id, f"不能含 {char!r}")
    return session_id


def new_uuidv7(timestamp_ms: int) -> str:
    """按 RFC 9562 的布局组装一个 UUIDv7 字符串。"""
    random_bits = secrets.token_bytes(10)
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= int.from_bytes(random_bits[:2], "big") & 0x0FFF
    value |= 0b10 << 62
    value |= int.from_bytes(random_bits[2:], "big") & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))


class UuidV7Generator:
    """默认的 id 生成器。``now`` 返回整数毫秒。"""

    def __init__(self, now=now_ms) -> None:
        self._now = now

    def next(self) -> str:
        return new_uuidv7(self._now())
