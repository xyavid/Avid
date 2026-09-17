"""工具注册表：把定义（TOOLS）与实现（TOOL_IMPLS）绑定在一起。

两者的 name 必须一一对应；定义形式的完整性由 tests/test_tools_contract.py 校验。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .files import edit_file, glob_files, read_file, write_file
from .schemas import (
    BASH,
    EDIT_FILE,
    GLOB,
    LOAD_SKILL,
    READ_FILE,
    SUBAGENT,
    TODO_WRITE,
    WRITE_FILE,
)
from .shell import bash
from .skill import load_skill
from .subagent import subagent
from ..policy.todo import todo_write

# 参数放宽为 ...：多数工具是 (args)，需要运行状态的少数几个是 (args, *, state)。
# 后者由 execution.STATEFUL_TOOLS 显式列出，契约测试校验它不漏不错。
ToolImpl = Callable[..., Any]

TOOLS: list[dict[str, Any]] = [
    BASH,
    READ_FILE,
    WRITE_FILE,
    EDIT_FILE,
    GLOB,
    TODO_WRITE,
    SUBAGENT,
    LOAD_SKILL,
]

TOOL_IMPLS: dict[str, ToolImpl] = {
    "bash": bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob_files,
    "todo_write": todo_write,
    "subagent": subagent,
    "load_skill": load_skill,
}

# 子 agent 的工具集：去掉 subagent 本身，结构上不可能递归派生。
SUB_TOOLS: list[dict[str, Any]] = [
    item for item in TOOLS if item["function"]["name"] != "subagent"
]
SUB_HANDLERS: dict[str, ToolImpl] = {
    name: impl for name, impl in TOOL_IMPLS.items() if name != "subagent"
}
