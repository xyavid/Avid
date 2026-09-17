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

SUBAGENT = tool(
    "subagent",
    "把互不依赖的子任务派给多个 subagent 并行处理，全部结束后汇总各自的结果。"
    "【只在任务可拆分、且子任务之间没有共享状态与先后依赖时使用】："
    "存在强依赖、需要共享同一份上下文、或一步就能做完的，不要用，直接自己做。"
    "subagent 看不到你和用户的对话，只会收到你在 prompt 里写的那段说明——"
    "所以每条任务都要自包含：写清背景、要做什么、期望的输出格式。"
    "一次最多 4 个。",
    {
        "tasks": {
            "type": "array",
            "description": "要并行处理的子任务，每项互相独立、没有先后顺序。",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "一句话说明这个子任务干什么，用于在汇总结果里标注归属。",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "发给该 subagent 的完整指令，自包含：背景、要做什么、期望输出。",
                    },
                },
                "required": ["description", "prompt"],
                "additionalProperties": False,
            },
        }
    },
    ("tasks",),
)

LOAD_SKILL = tool(
    "load_skill",
    "读取某个技能的完整说明。可用技能列在系统提示的「可用技能」里。"
    "当手上的任务命中某个技能时，先调它把完整步骤读出来再动手——"
    "目录里只有一句话，真正的操作步骤和坑都在全文里。",
    {
        "name": {
            "type": "string",
            "description": "技能名，必须与技能目录里列出的名字完全一致。",
        }
    },
    ("name",),
)


# ---------------- 任务图（Task DAG，阶段 13） ----------------
#
# 与 todo_write 的分工：todo_write 是"本次运行内的清单"，任务是"跨会话的图"
# （有稳定 ID、依赖与归属）。建图固定两阶段：先批量 create_task 拿 ID，再用
# update_task 加边——同一条回复里的多个工具调用互相看不到结果。

_TASK_ID = {
    "type": "string",
    "description": "任务 ID，形如 task_1a2b3c4d（create_task 的返回值）。",
}

_OWNER = {
    "type": "string",
    "description": "认领者标识，默认 agent；多个执行者时要写清是谁。",
}

CREATE_TASK = tool(
    "create_task",
    "创建一个任务节点，返回它的运行时 ID。任务存在 .tasks/ 里，跨会话存活："
    "blockedBy 表达依赖、owner 表达谁在做。新任务的 blockedBy 固定为空。"
    "【建图分两阶段】：先用本工具把全部节点建出来拿到 ID，下一轮再用 update_task 加依赖；"
    "同一条回复里的多个工具调用互相看不到结果，因此不能引用彼此刚生成的 ID。",
    {
        "subject": {
            "type": "string",
            "description": "任务标题，一句话，不能为空。",
        },
        "description": {
            "type": "string",
            "description": "可选。完整描述（背景、验收口径），跨会话恢复时靠它继续工作。",
        },
    },
    ("subject",),
)

UPDATE_TASK = tool(
    "update_task",
    "给一个任务加前置依赖（blockedBy），也就是在任务图上连边。"
    "只能在节点都已创建之后调用，用 create_task 返回的 ID。"
    "目标必须是 pending 且还没人认领；依赖必须已存在；不能自依赖或成环"
    "（重复添加同一条依赖是安全的）。"
    "整次修改先校验再统一保存：任何一条不合法，这次调用不会改任何东西。",
    {
        "task_id": _TASK_ID,
        "addBlockedBy": {
            "type": "array",
            "description": "要加的前置任务 ID（这些任务全部 completed 之后本条才能开始）。",
            "items": {"type": "string"},
        },
    },
    ("task_id", "addBlockedBy"),
)

CAN_START = tool(
    "can_start",
    "查询一条任务现在能不能开始：blockedBy 全部 completed（且依赖文件都还在）才返回 True。"
    "认领之前先问一次，避免把轮次浪费在被挡住的任务上。",
    {"task_id": _TASK_ID},
    ("task_id",),
)

CLAIM_TASK = tool(
    "claim_task",
    "认领任务：状态从 pending 改成 in_progress 并记下 owner，表示你开始做它了。"
    "任务不是 pending、或依赖没做完都会被拒绝（拒绝文本会说明原因，不要重复提交）。",
    {"task_id": _TASK_ID, "owner": _OWNER},
    ("task_id",),
)

COMPLETE_TASK = tool(
    "complete_task",
    "把正在做的任务标记为 completed，并报告因此被解锁的下游任务（只报本次新解锁的）。"
    "只有认领它的那个 owner 能完成；状态不是 in_progress、或 owner 不匹配都会被拒绝。",
    {"task_id": _TASK_ID, "owner": _OWNER},
    ("task_id",),
)

GET_TASK = tool(
    "get_task",
    "读取一条任务的完整 JSON（含 description 与 blockedBy）。"
    "跨会话恢复、或需要看清依赖细节时用它——列表里只有一行摘要。",
    {"task_id": _TASK_ID},
    ("task_id",),
)
