"""变更线：把"读-改-写"整段串行化。

一次 ``append_message`` 是三件事：读分支头、写条目、写新分支头。它们必须落在
同一个事务里，中间不能插进别人的写——否则两条链会争同一个头，后写的把先写的
覆盖掉（丢更新）。``MutationLine`` 就是这道闸门：一次只放一个作业进来。

两处与参考实现的差异（取舍 A5），都是同步语义逼出来的：

* pi 里在 ``mutate`` 回调内再调用公开写方法会**排队**（然后是死锁风险，注释里
  明写了这一点）；同步阻塞的 Python 里排队必自锁，所以这里直接报
  ``SessionBusyError``——更早失败，且错误信息能指出该怎么做。
* 关闭时会 ``seal`` 变更线：正在等待的线程立刻拿到"已关闭"，而已经持有闸门的
  作业可以把它那一次提交做完（``wait_idle`` 等它结束）。
"""

from __future__ import annotations

import threading

from .errors import SessionBusyError, SessionClosedError


class MutationLine:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._owner: int | None = None
        self._sealed: SessionClosedError | None = None

    def acquire(self) -> None:
        """拿到闸门；被别人占着就等（FIFO 由 Condition 的等待队列保证）。"""
        me = threading.get_ident()
        with self._condition:
            if self._sealed is not None:
                raise self._sealed
            if self._owner == me:
                raise SessionBusyError(
                    "同一个线程在变更回调里又发起了变更。"
                    "请把这些写合并进同一次 commit，而不要在回调里调用公开写方法。"
                )
            while self._owner is not None:
                self._condition.wait()
                if self._sealed is not None:
                    raise self._sealed
            self._owner = me

    def release(self) -> None:
        with self._condition:
            self._owner = None
            self._condition.notify_all()

    def held_by_current_thread(self) -> bool:
        with self._condition:
            return self._owner == threading.get_ident()

    def seal(self, error: SessionClosedError) -> None:
        """封线：之后 acquire 一律失败；已经在跑的作业不受影响。"""
        with self._condition:
            if self._sealed is None:
                self._sealed = error
            self._condition.notify_all()

    @property
    def sealed_error(self) -> SessionClosedError | None:
        return self._sealed

    def wait_idle(self) -> None:
        """等当前持有的作业结束（对应 pi 的 drain）。"""
        with self._condition:
            while self._owner is not None:
                self._condition.wait()
