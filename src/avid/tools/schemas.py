"""工具定义：给模型看的 schema。

形式沿用 OpenAI function calling（D-03 / D-08）：
``{"type": "function", "function": {name, description, parameters}}``。
所有定义都经 tool() 生成，信封字段与 additionalProperties 因此不会漏写。

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


BASH = tool(
    "bash",
    "在工作区根目录执行一条 shell 命令，返回合并后的 stdout/stderr 与退出码。"
    "适合运行测试、构建、git、批量文本处理。每次调用都是独立的新 shell——"
    "需要切换目录时在同一条命令里用 cd。读写单个文件请优先用专用工具。",
    {
        "command": {
            "type": "string",
            "description": "要执行的 shell 命令，通过 bash -lc 运行。",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "超时秒数，默认 30，上限 300；超时后进程被终止。",
        },
    },
    ("command",),
)

READ_FILE = tool(
    "read_file",
    "读取工作区内文本文件的内容，按行返回。默认从第 1 行起最多读 2000 行，"
    "超过 20000 字符的部分会被截断；两种情况都会在末尾提示，可用 offset / limit 继续读。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
        },
        "offset": {
            "type": "integer",
            "description": "起始行号，从 1 开始，默认 1。",
        },
        "limit": {
            "type": "integer",
            "description": "本次最多读取的行数，默认 2000。",
        },
    },
    ("path",),
)

WRITE_FILE = tool(
    "write_file",
    "把内容整体写入工作区内的文件：文件已存在则覆盖，缺失的父目录会自动创建。"
    "只改几行请用 edit_file，不要为了局部修改而整体重写。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
        },
        "content": {
            "type": "string",
            "description": "要写入的完整文件内容，UTF-8 编码。",
        },
    },
    ("path", "content"),
)

EDIT_FILE = tool(
    "edit_file",
    "把文件中的 old_string 精确替换为 new_string，只替换一次。"
    "old_string 必须在文件中恰好出现一次：出现 0 次或多次都不做修改并报错，"
    "多次时需要附带更多上下文让它唯一。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，相对工作区根目录；工作区内的绝对路径也可。",
        },
        "old_string": {
            "type": "string",
            "description": "要被替换的原文，必须与文件内容逐字符一致且在文件中唯一。",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的文本；传空字符串表示删除这段内容。",
        },
    },
    ("path", "old_string", "new_string"),
)

GLOB = tool(
    "glob",
    "按 glob 模式在工作区内查找文件，返回相对路径列表，例如 **/*.py、src/**/*.md。"
    "只匹配文件名，不搜索文件内容；* 不匹配以点开头的文件。",
    {
        "pattern": {
            "type": "string",
            "description": "glob 模式，例如 **/*.py。",
        },
        "path": {
            "type": "string",
            "description": '起始目录，相对工作区根目录，默认 "."。',
        },
    },
    ("pattern",),
)

TODO_WRITE = tool(
    "todo_write",
    "整份替换当前任务的 TODO 列表，用来把多步任务显式计划出来并跟踪进度。"
    "每次调用都要提交【完整】列表，不是增量；开始多步任务前先调用一次，"
    "之后每完成一步就更新对应项的状态并重新提交整份列表。"
    "只有一步、或不需要跟踪进度时不必调用。",
    {
        "todos": {
            "type": "array",
            "description": "完整 TODO 列表，按执行顺序排列；空数组表示清空。",
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "这一步要做什么，一句话。",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "该步状态：未开始 / 进行中 / 已完成。",
                    },
                },
                "required": ["content", "status"],
                "additionalProperties": False,
            },
        }
    },
    ("todos",),
)
