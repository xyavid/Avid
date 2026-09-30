"""Value addresses: session state sharing the entry sequence; values are JSON scalars or small objects, never lists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import ValueDeleteWrite, ValueSetWrite

SESSION_NAME_NS = "avid.session.name"
ENTRY_LABEL_NS = "avid.entry.label"
BRANCH_TIP_NS = "avid.branch.tip"
# Run-usage ledger, one address per branch: usage lives only inside a run, so it is persisted to survive a reload.
USAGE_NS = "avid.usage"

# The implicit default branch; a session with no branch value yet is read as if it were on main.
DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class ValueAddress:
    """A namespace plus key pair that addresses one value; the empty key denotes the whole namespace."""

    namespace: str
    key: str = ""


def value(namespace: str, key: str = "") -> ValueAddress:
    """Build an address, rejecting an empty namespace or an embedded NUL because those are programming errors."""
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
    """Usage is booked per branch, not per session, because each chain carries a different context."""
    return ValueAddress(USAGE_NS, branch)


def set_value(address: ValueAddress, next_value: Any) -> ValueSetWrite:
    return ValueSetWrite(address.namespace, address.key, next_value)


def delete_value(address: ValueAddress) -> ValueDeleteWrite:
    return ValueDeleteWrite(address.namespace, address.key)
