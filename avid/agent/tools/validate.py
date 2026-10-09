"""Checks tool arguments against the declared schema before the implementation runs.

A wrong-typed argument would otherwise reach the tool as a generic execution failure.

"""


from __future__ import annotations

from typing import Any

# JSON Schema type to the Python expectation; bool is excluded separately since it subclasses int.
_SCALARS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _type_name(value: Any) -> str:
    """Names a Python value's JSON Schema type for the argument-error text."""
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
    """Wraps one argument problem in the prefix that marks it as a protocol error, so the model
    can tell it apart from a tool fault or a business refusal."""
    return f"参数错误：{problem}；请按工具 schema 修正后重试。"


def _bad(path: str, problem: str) -> str:
    """Prefixes a problem with its argument path and wraps it in the argument-error style."""
    return bad_arguments(f"{path} {problem}" if path else problem)


def _field(path: str, name: str) -> str:
    """Joins a parent path with a field name, or returns the bare name at the top level."""
    return f"{path}.{name}" if path else name


def _check(spec: dict[str, Any], value: Any, path: str) -> str | None:
    """Validates one value recursively; returns None on success or text for the model."""
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
    """Validates one tool call's arguments against the parameters node sent to the model,
    returning None on success or text the model can act on."""
    return _check(parameters, arguments, "")
