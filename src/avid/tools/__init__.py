"""工具注册表：定义与实现都来自 registry 的**单点声明**，这里只做派生。

一个工具的全部事实（schema / 实现 / 并发分类 / 是否需要 state）在它自己的模块里用
``@tool`` 装饰器声明（见 ``registry.py``）；本模块只负责触发加载并派生出循环与执行
环节消费的几张表。新增工具不再改这里。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .registry import ToolSpec, ensure_loaded, specs

# 参数放宽为 ...：多数工具是 (args)，需要运行状态的少数几个是 (args, *, state)。
ToolImpl = Callable[..., Any]

ensure_loaded()

TOOLS: list[dict[str, Any]] = [spec.schema() for spec in specs()]

TOOL_IMPLS: dict[str, ToolImpl] = {spec.name: spec.impl for spec in specs()}

# 子 agent 的工具集：去掉 subagent 本身，结构上不可能递归派生。
SUB_TOOLS: list[dict[str, Any]] = [
    item for item in TOOLS if item["function"]["name"] != "subagent"
]
SUB_HANDLERS: dict[str, ToolImpl] = {
    name: impl for name, impl in TOOL_IMPLS.items() if name != "subagent"
}

__all__ = [
    "SUB_HANDLERS",
    "SUB_TOOLS",
    "TOOLS",
    "TOOL_IMPLS",
    "ToolImpl",
    "ToolSpec",
    "specs",
]
