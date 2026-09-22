"""值地址：会话级的小块状态，与条目共用同一条 seq。

pi 把"不是消息但必须持久化"的东西一律做成值——分支头、会话名、条目标签、
待定帧、操作状态，于是存储层只需要两种东西：条目与地址化的值。Avid 沿用这个
形状，值取 JSON 标量或**小对象**（`avid.usage` 的运行用量快照是第一个对象值），
但**不做列表与 prefix 扫描**（取舍 A4：那两样的使用者只有 fork 与待定帧）。

地址里的 namespace 与 key 都不允许空 namespace 或 NUL——它们是程序错误，
不是运行时状况，所以抛 ``TypeError``（与 pi 一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import ValueDeleteWrite, ValueSetWrite

SESSION_NAME_NS = "avid.session.name"
ENTRY_LABEL_NS = "avid.entry.label"
BRANCH_TIP_NS = "avid.branch.tip"
# 运行用量台账（阶段 22）：每个分支一个地址，值是 `RunState.usage_report()` 的快照。
# 为什么要落盘：刷新页面、切换会话、重启 `avid web` 之后，界面仍要能显示"这个分支
# 上一次运行用了多少上下文、缓存命中了多少"——这些数只活在一次运行里。
USAGE_NS = "avid.usage"

# 默认分支名。单点定义在这里（值的地址就是分支的表示），recorder 与读侧都引用它。
# 会话 `create` 不隐式建分支，所以「还没有任何分支值」的会话由读侧把 main 视作
# 隐式默认——否则新建会话在界面上会显示成「没有分支」，而它随时可以往 main 写。
DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class ValueAddress:
    namespace: str
    key: str = ""


def value(namespace: str, key: str = "") -> ValueAddress:
    """构造一个地址。空 namespace 或含 NUL 直接报错。"""
    if not namespace:
        raise TypeError("值地址的 namespace 不能为空")
    if "\u0000" in namespace:
        raise TypeError("值地址的 namespace 不能含 NUL")
    if "\u0000" in key:
        raise TypeError("值地址的 key 不能含 NUL")
    return ValueAddress(namespace, key)


def session_name() -> ValueAddress:
    return ValueAddress(SESSION_NAME_NS, "")


def entry_label(entry_id: str) -> ValueAddress:
    return ValueAddress(ENTRY_LABEL_NS, entry_id)


def branch_tip(branch: str) -> ValueAddress:
    return ValueAddress(BRANCH_TIP_NS, branch)


def branch_usage(branch: str) -> ValueAddress:
    """某分支最近一次运行的用量快照。

    按**分支**记账而不是按会话：运行总是跑在某条链上，不同链的上下文本来就不同，
    合成一个数会让"切到另一条分支"显示成上一条链的占用。
    """
    return ValueAddress(USAGE_NS, branch)


def set_value(address: ValueAddress, next_value: Any) -> ValueSetWrite:
    return ValueSetWrite(address.namespace, address.key, next_value)


def delete_value(address: ValueAddress) -> ValueDeleteWrite:
    return ValueDeleteWrite(address.namespace, address.key)
