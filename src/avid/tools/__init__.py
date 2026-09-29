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


def build_toolset(state: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
    """本次运行下发给模型的工具集：内置注册表 + 该工作区声明的 MCP 工具。

    MCP server 由运行入口装配进 ``state.mcp``（进程随 run 起停，阶段 30e）；没有它
    就是纯内置工具集——CLI 裸路径与全部既有用例的行为不变。MCP 工具排在内置之后，
    名字自带 ``mcp__<server>__<tool>`` 前缀，闸门与前端据此认识它们。
    """
    schemas = list(TOOLS)
    impls = dict(TOOL_IMPLS)
    manager = getattr(state, "mcp", None)
    if manager is not None:
        extra_schemas, extra_impls = manager.toolset()
        schemas.extend(extra_schemas)
        impls.update(extra_impls)
    return schemas, impls

__all__ = [
    "SUB_HANDLERS",
    "SUB_TOOLS",
    "TOOLS",
    "TOOL_IMPLS",
    "ToolImpl",
    "ToolSpec",
    "build_toolset",
    "specs",
]
