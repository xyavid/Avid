"""工具参数的结构校验：schema 里写的约束必须在调用前真的查一遍。

模型给出的参数以前只过 ``json.loads`` 与「是不是对象」，之后原样交给实现：``{"offset":
"abc"}`` 会一路进到工具里变成 ``TypeError``，最后兜底成「工具 X 执行失败」——模型分不清
是参数写错了还是环境有问题，只能瞎试。这里有三个事实：模型看到的是 schema（`schemas.py`
手写）、实现按自己的假设取参数、两者之间以前没有任何检查。

只查 schema 能表达、且实现真的依赖的三类约束：``required`` 缺失、``type`` 不符、
``enum`` 越界，外加 integer/number 的 ``minimum``/``maximum``；数组与嵌套对象按
``items`` / ``properties`` 递归。**不查 ``additionalProperties``**：多带的键现在是被
实现忽略的，收紧它属于行为变更，等出现"模型乱加键"的真实证据再说。跨字段规则
（「同时只能有一项 in_progress」）留在工具自己的领域校验里——那是业务规则，不是结构。

校验用的 ``parameters`` 就是发给模型的那一份（``tool()`` 产出的节点），不另抄一份，
因此「模型看到的定义」与「运行时校验」不可能漂移。
"""

from __future__ import annotations

from typing import Any

# JSON Schema 的 type → Python 侧期望。`bool` 是 `int` 的子类，必须单独排除，
# 否则 `True` 会被当成合法的 integer。
_SCALARS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _type_name(value: Any) -> str:
    """实际类型名——用 JSON 的词表，模型才看得懂（不是 Python 的 `str`/`bool`）。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def bad_arguments(problem: str) -> str:
    """参数类失败的统一样式。

    模型要能一眼把「参数写错了」与「工具/环境出问题」（``工具执行失败：…``）和
    「业务拒绝」（``错误：…``）分开——三种失败该做的事完全不同：改参数、换做法、
    别重复提交。前缀与结尾提示是 ``web/schemas.py`` 判定工具状态的依据，样式只此一处。
    """
    return f"参数错误：{problem}；请按工具 schema 修正后重试。"


def _bad(path: str, problem: str) -> str:
    return bad_arguments(f"{path} {problem}" if path else problem)


def _field(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _check(spec: dict[str, Any], value: Any, path: str) -> str | None:
    """递归校验一个值；通过返回 None，否则返回回给模型的文本。"""
    expected = spec.get("type")

    if expected in _SCALARS and not isinstance(value, _SCALARS[expected]):
        return _bad(path, f"必须是 {expected}，实际是 {_type_name(value)}")
    if expected in ("integer", "number") and isinstance(value, bool):
        return _bad(path, f"必须是 {expected}，实际是 boolean")

    enum = spec.get("enum")
    if isinstance(enum, list) and value not in enum:
        options = "、".join(repr(item) for item in enum)
        return _bad(path, f"只能是 {options} 之一，实际是 {value!r}")

    if expected in ("integer", "number"):
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return _bad(path, f"不能小于 {minimum}，实际是 {value!r}")
        if isinstance(maximum, (int, float)) and value > maximum:
            return _bad(path, f"不能大于 {maximum}，实际是 {value!r}")

    if expected == "array" and isinstance(value, list):
        items = spec.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                problem = _check(items, item, f"{path}[{index}]")
                if problem is not None:
                    return problem

    if expected == "object" and isinstance(value, dict):
        for name in spec.get("required") or ():
            if name not in value:
                return _bad(_field(path, str(name)), "缺失（必填）")
        properties = spec.get("properties")
        if isinstance(properties, dict):
            for name, sub in properties.items():
                if name in value and isinstance(sub, dict):
                    problem = _check(sub, value[name], _field(path, str(name)))
                    if problem is not None:
                        return problem

    return None


def validate_arguments(
    parameters: dict[str, Any], arguments: dict[str, Any]
) -> str | None:
    """校验一次工具调用的参数。

    ``parameters`` 是 ``tool()`` 产出的 ``parameters`` 节点（与发给模型的同一份）。
    通过返回 None；不通过返回一句可直接回传给模型的文本——参数错误是**协议错误**，
    按既有约定回文本、不抛异常，也不触发工具事件（``execution.execute_one``）。
    """
    return _check(parameters, arguments, "")
