"""工具登记的**单点**（阶段 30b）。

一个工具的全部事实——给模型看的 schema、实现函数、并发分类、是否需要运行状态——
只在**一处**声明（``@tool`` 装饰器，与实现函数贴在一起）；``TOOLS`` / ``TOOL_IMPLS``
/ ``SUB_TOOLS`` / 并发两张表 / ``STATEFUL_TOOLS`` 全部从这里派生。此前这些事实分散在
schemas.py（定义）、tools/__init__.py（绑定）、safety.py（分类）、execution.py
（stateful 清单）四处，靠契约测试对齐——新增一个工具要改 5 处，漏一处只会在运行时
以 TypeError 的形式暴露。现在新增工具 = 在它的实现上挂一个装饰器。

登记顺序即派生顺序：``ensure_loaded`` 里的 import 顺序决定 TOOLS 顺序（发给模型的
工具清单顺序），所以那里保持与历史顺序一致（bash 在前）。

``specs()`` 幂等触发加载，任何消费方（safety、tools/__init__、契约测试）都不必关心
先 import 谁。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .schemas import tool as _envelope

#: 参数放宽为 ...：多数工具是 (args)，需要运行状态的少数几个是 (args, *, state)。
ToolImpl = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明。"""

    name: str
    description: str
    parameters: dict[str, Any]
    impl: ToolImpl
    #: "safe" = 只读、可与同段调用并发；"exclusive" = 屏障，单独跑（tools/safety.py）。
    concurrency: str
    #: 实现是否接受 ``state=``（缺省从签名推断）。
    stateful: bool

    def schema(self) -> dict[str, Any]:
        """给模型看的 OpenAI function calling 信封。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_SPECS: list[ToolSpec] = []
_LOADED = False


def _register(spec: ToolSpec) -> None:
    if any(item.name == spec.name for item in _SPECS):
        raise ValueError(f"工具 {spec.name} 重复登记")
    _SPECS.append(spec)


def tool(
    *,
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: tuple[str, ...] = (),
    concurrency: str,
    stateful: bool | None = None,
) -> Callable[[ToolImpl], ToolImpl]:
    """声明一个工具：schema 与实现写在一处，并发分类必须显式表态。

    ``stateful`` 缺省从实现签名推断（有 ``state`` 关键字参数即需要运行状态）——
    它本来就是签名的可观察事实，不该第二处手抄。
    """
    if concurrency not in ("safe", "exclusive"):
        raise TypeError(
            f"工具 {name} 必须显式声明 concurrency='safe' 或 'exclusive'（拿不准就 exclusive）"
        )

    def decorate(fn: ToolImpl) -> ToolImpl:
        inferred = stateful
        if inferred is None:
            inferred = "state" in inspect.signature(fn).parameters
        envelope = _envelope(name, description, properties, required)
        _register(
            ToolSpec(
                name=name,
                description=description,
                parameters=envelope["function"]["parameters"],
                impl=fn,
                concurrency=concurrency,
                stateful=bool(inferred),
            )
        )
        return fn

    return decorate


def ensure_loaded() -> None:
    """导入全部工具模块，触发登记。幂等；顺序即 TOOLS 顺序（保持历史顺序）。"""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import files, shell, skill, subagent, web_search  # noqa: F401
    from ..policy import todo  # noqa: F401  （todo_write 的实现住在策略层，历史如此）


def specs() -> tuple[ToolSpec, ...]:
    ensure_loaded()
    return tuple(_SPECS)
