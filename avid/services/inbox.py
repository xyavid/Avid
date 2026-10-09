"""会话级 Inbox：已接收、还没被采纳的输入（阶段 60）。

「补充输入」有两种交付模式，共用这一张表：

- ``now``：**最早可能被处理的时刻**。有活动 run 就在它的下一个 step 边界交付
  （run 线程来领）；空闲就直接起一个 run。客户端因此不用自己处理「我以为是忙的、
  其实刚跑完」这个竞态——那是服务端一句话的事。
- ``after``：**等下一 turn**。留在队里，由客户端在终态事件之后领取起一个 run。

三条不变量（每条都有用例钉着）：

1. **被接受的输入永不消失**：进当前 turn 的 step、留在队里、或在入口被明确拒绝
   （409 / 400），没有第四种结局。所以 run 结束前没赶上的 ``now`` 不是「未送达」，
   而是降级成 ``after``（``missed=True``），照旧留在队里。
2. **服务端不自己起 run**：无人看管的 run 会卡在审批上（approvals 超时按拒绝处理），
   那是假的「自动」。队列跨刷新、不跨进程（run 本身也不跨进程）。
3. **领取是原子的**：两个标签页同抢，只有一个 ``take_for_run`` 会成功。

这一层不是第二份会话真相：它只持有「还没被采纳的输入」，采纳的唯一出口仍是
``SessionRecorder``（run 线程领取后落库）。与 ``ApprovalTable`` 一样是进程内存态。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from ..agent.events import now_ms

#: 两种交付模式；别的一律拒（不做「语义差不多」的第三档）。
MODE_NOW = "now"
MODE_AFTER = "after"
MODES = (MODE_NOW, MODE_AFTER)


@dataclass(frozen=True)
class PendingInput:
    """一条待采纳的输入。

    ``content`` 已经是存储形态（str | 分块数组，见 avid/attachments.py），
    ``params`` 是它起 run 时要用的那些开关（model / effort / branch / full_access_ack）——
    排队项自带它们，客户端领取时不必重发，也就不会与投递时的意图漂移。
    """

    input_id: str
    mode: str
    content: Any
    params: dict[str, Any] = field(default_factory=dict)
    client_id: str | None = None
    created_at: int = 0
    #: 从 now 降级来的（run 在它被领取前就结束了）：界面照实说「没赶上，已排队」。
    missed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """线格式：内容只给一段预览与图片张数（字节走会话侧的读端点）。"""
        from .. import attachments

        return {
            "input_id": self.input_id,
            "mode": self.mode,
            "text": attachments.render_content_text(self.content),
            "images": attachments.image_count(self.content),
            "client_id": self.client_id,
            "created_at": self.created_at,
            "missed": self.missed,
        }


class SessionInbox:
    """一个会话的待办输入；所有方法都在自己的锁里。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._lock = threading.RLock()
        self._items: list[PendingInput] = []

    def add(
        self,
        *,
        mode: str,
        content: Any,
        params: dict[str, Any] | None = None,
        client_id: str | None = None,
    ) -> PendingInput:
        """收下一条输入；同 ``client_id`` 的重复投递返回已有那条（幂等）。

        ``created_at`` 由收下它的这一刻决定（不是客户端说的时刻）：它是“谁先来”的唯一依据。
        """
        if mode not in MODES:
            raise ValueError(f"mode 只能是 {' / '.join(MODES)}，收到 {mode!r}")
        with self._lock:
            if client_id:
                for existing in self._items:
                    if existing.client_id == client_id:
                        return existing
            item = PendingInput(
                input_id=f"in_{uuid.uuid4().hex[:12]}",
                mode=mode,
                content=content,
                params=dict(params or {}),
                client_id=client_id,
                created_at=now_ms(),
            )
            self._items.append(item)
            return item

    def pending(self) -> list[PendingInput]:
        with self._lock:
            return list(self._items)

    def take_steers(self) -> list[PendingInput]:
        """取走队里全部 ``now``（run 线程在轮次边界调用）；按投递顺序。"""
        with self._lock:
            taken = [item for item in self._items if item.mode == MODE_NOW]
            self._items = [item for item in self._items if item.mode != MODE_NOW]
            return taken

    def take_for_run(self, input_id: str) -> PendingInput | None:
        """原子领取一条去起 run；已被取走 / 不存在 / 已撤销都返回 None。"""
        with self._lock:
            for index, item in enumerate(self._items):
                if item.input_id == input_id:
                    del self._items[index]
                    return item
            return None

    def downgrade_steers(self) -> list[PendingInput]:
        """run 结束：把还没被领走的 ``now`` 降级成 ``after``（标 missed），仍然留在队里。"""
        with self._lock:
            downgraded: list[PendingInput] = []
            items: list[PendingInput] = []
            for item in self._items:
                if item.mode == MODE_NOW:
                    item = replace(item, mode=MODE_AFTER, missed=True)
                    downgraded.append(item)
                items.append(item)
            self._items = items
            return downgraded

    def restore(self, item: PendingInput) -> None:
        """把刚领走的一条放回队首（起 run 失败时用）：输入不消失，顺序也不乱。"""
        with self._lock:
            if all(existing.input_id != item.input_id for existing in self._items):
                self._items.insert(0, item)

    def remove(self, input_id: str) -> bool:
        """撤销一条**尚未领取**的输入；已领走的返回 False（那是会话里的事实了）。"""
        with self._lock:
            for index, item in enumerate(self._items):
                if item.input_id == input_id:
                    del self._items[index]
                    return True
            return False

    def empty(self) -> bool:
        with self._lock:
            return not self._items


__all__ = ["MODE_AFTER", "MODE_NOW", "MODES", "PendingInput", "SessionInbox"]
