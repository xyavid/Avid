"""工具 schema 的信封构造器（阶段 30b 起只此一职）。

形式沿用 OpenAI function calling（D-03 / D-08）：
``{"type": "function", "function": {name, description, parameters}}``。
所有定义都经 tool() 生成，信封字段与 additionalProperties 因此不会漏写；
各工具的**完整声明**（schema + 实现 + 并发分类）在各自的实现模块里用
``registry.tool`` 装饰器完成，那里复用这里的信封构造。

不启用 ``strict: true``：并非所有 OpenAI 兼容端点支持，而"参数不合法"
在本项目里本来就是可回传给模型的错误，不需要靠协议层拦截。
"""

from __future__ import annotations

from typing import Any

Property = dict[str, Any]


def tool(
    name: str,
    description: str,
    properties: dict[str, Property],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """统一封装函数工具信封。参数一律扁平对象，不嵌套。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }
