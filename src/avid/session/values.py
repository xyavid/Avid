"""值地址：会话级的小块状态，与条目共用同一条 seq。

pi 把"不是消息但必须持久化"的东西一律做成值——分支头、会话名、条目标签、
待定帧、操作状态，于是存储层只需要两种东西：条目与地址化的值。Avid 沿用这个
形状，但只做标量（取舍 A4：列表与 prefix 扫描的使用者只有 fork 与待定帧，
两者都不在本阶段），保留地址见下。

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


def set_value(address: ValueAddress, next_value: Any) -> ValueSetWrite:
    return ValueSetWrite(address.namespace, address.key, next_value)


def delete_value(address: ValueAddress) -> ValueDeleteWrite:
    return ValueDeleteWrite(address.namespace, address.key)
