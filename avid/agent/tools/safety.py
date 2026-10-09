"""Answers which tool calls in one batch run concurrently.

三条规则，按顺序：

1. 声明为 ``safe`` 的重叠执行，``exclusive`` 是屏障；未知工具名按独占（默认拒绝）；
2. ``conditional`` 的问 ``assess(arguments, state)``——**只有答 "safe" 才算并行**，
   抛异常、返回别的值、没有 assessor 都是独占；
3. 声明 ``writes=True`` 的工具永远不并行（声明期已拦下 conditional，这里是第二道）。
"""


from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .registry import specs

logger = logging.getLogger("avid.agent.tools.safety")

#: Read-only tools: any number of these may overlap inside one segment.
CONCURRENCY_SAFE: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "safe"
)

#: Tools that write, spawn processes or touch shared state: each runs alone as a barrier.
EXCLUSIVE: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "exclusive"
)

#: Tools that decide per call: the assessor may downgrade them to safe, never the other way.
CONDITIONAL: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "conditional"
)


#: Tool name -> declaration, so a call's own arguments can be asked about.
_BY_NAME = {spec.name: spec for spec in specs()}


def is_concurrency_safe(
    name: str, arguments: Mapping[str, Any] | None = None, state: Any = None
) -> bool:
    """Reports whether this call may overlap with its segment peers; unknown names are unsafe."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return False
    if spec.concurrency == "safe":
        return True
    if spec.concurrency != "conditional" or spec.assess is None or spec.writes:
        return False
    try:
        return spec.assess(dict(arguments or {}), state) == "safe"
    except Exception:  # 误判面收敛：判定本身出错就当独占
        logger.warning("工具 %s 的并发判定抛错，按独占处理", name, exc_info=True)
        return False


__all__ = ["CONCURRENCY_SAFE", "CONDITIONAL", "EXCLUSIVE", "is_concurrency_safe"]
