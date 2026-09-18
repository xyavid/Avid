"""A7：两侧事件清单一致（机械检查，不上生成器）。

``runtime/events.py`` 的 ``EVENT_TYPES`` 与 ``web/src/events/types.ts`` 里的联合
类型成员集合必须**相等**。这是 D8 的落地方式：在「事件数量 × 变更频率」超过人工
同步成本之前，一条集合相等测试比一套生成器便宜（设计文档 §6.3）。
"""

from __future__ import annotations

import re
from pathlib import Path

from avid.runtime.events import DELTA_EVENT_TYPES, DURABLE_EVENT_TYPES, EVENT_TYPES, TRANSIENT_EVENT_TYPES

ROOT = Path(__file__).resolve().parents[1]
TYPES_TS = ROOT / "web" / "src" / "events" / "types.ts"

_BLOCK = re.compile(r"// EVENTS:BEGIN(.*?)// EVENTS:END", re.S)
_MEMBER = re.compile(r"['\"]([a-z_]+)['\"]")


def frontend_event_types() -> set[str]:
    text = TYPES_TS.read_text(encoding="utf-8")
    match = _BLOCK.search(text)
    assert match is not None, "types.ts 缺少 EVENTS:BEGIN / EVENTS:END 标记块"
    return set(_MEMBER.findall(match.group(1)))


def test_frontend_and_kernel_event_lists_are_equal():
    assert frontend_event_types() == set(EVENT_TYPES)


def test_frontend_declares_the_same_tiers():
    text = TYPES_TS.read_text(encoding="utf-8")
    for pivot in DURABLE_EVENT_TYPES:
        assert f'"{pivot}"' in text or f"'{pivot}'" in text
    assert set(DURABLE_EVENT_TYPES) | set(TRANSIENT_EVENT_TYPES) | set(
        DELTA_EVENT_TYPES
    ) == set(EVENT_TYPES)


def test_event_type_literals_are_unique_and_non_empty():
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES))
    assert all(name.strip() for name in EVENT_TYPES)
