"""工具定义与实现。

每个工具两件东西：给模型看的 schema（在 TOOLS 里），和真正执行的函数
（在 TOOL_IMPLS 里）。两者的一致性由 tests/test_tools.py 保证。

阶段 5 会引入正式权限模型；本轮只有一条最小护栏——只读工作区内的路径。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

# 在 import 时固定，测试里用 monkeypatch 替换。
WORKSPACE_ROOT = Path.cwd()

MAX_CHARS = 20000

ToolImpl = Callable[[dict[str, Any]], Any]

READ_FILE = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取工作区内的文本文件并返回其内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
                }
            },
            "required": ["path"],
        },
    },
}


def read_file(args: dict[str, Any]) -> str:
    raw = str(args.get("path", "")).strip()
    if not raw:
        return "缺少参数 path"

    path = Path(raw)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    path = path.resolve()

    root = WORKSPACE_ROOT.resolve()
    if path != root and root not in path.parents:
        return f"拒绝访问工作区外的路径：{raw}"
    if not path.exists():
        return f"文件不存在：{raw}"
    if path.is_dir():
        return f"{raw} 是目录，不是文件"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读取 {raw} 失败：{exc}"

    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + f"\n…（已截断，原文 {len(text)} 字符）"
    return text


TOOLS: list[dict[str, Any]] = [READ_FILE]

TOOL_IMPLS: dict[str, ToolImpl] = {"read_file": read_file}
