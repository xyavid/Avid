"""工具注册表：把定义（TOOLS）与实现（TOOL_IMPLS）绑定在一起。

两者的 name 必须一一对应；定义形式的完整性由 tests/test_tools_contract.py 校验。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .files import edit_file, glob_files, read_file, write_file
from .schemas import BASH, EDIT_FILE, GLOB, READ_FILE, WRITE_FILE
from .shell import bash

ToolImpl = Callable[[dict[str, Any]], Any]

TOOLS: list[dict[str, Any]] = [BASH, READ_FILE, WRITE_FILE, EDIT_FILE, GLOB]

TOOL_IMPLS: dict[str, ToolImpl] = {
    "bash": bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob_files,
}
