"""执行前的权限校验：三道闸门。

顺序固定，先严后宽：

1. **硬拒绝** —— 命中黑名单即拒绝，用户无法覆盖（``--yes`` 也不行）
2. **规则匹配** —— 按工具名判断是否需要审批，未命中直接放行
3. **用户审批** —— 交互式确认；读不到输入（EOF）视为拒绝

这不是沙箱，也不替代沙箱。黑名单挡的是"手滑一次就不可恢复"的命令；
变量展开、base64、引号拼接都能绕过它。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("avid.policy.permission")

# 命令起始位置：行首，或 ; & | 之后；跳过程序路径前缀与常见包装命令。
# 用它锚定，避免 `grep halt file` 这类把关键字当参数的误伤。
# 注意：env FOO=1 dd ... 这种插了变量赋值的写法仍能绕过——黑名单只是护栏。
_CMD_START = (
    r"(?:^|[;&|]\s*)(?:(?:sudo|command|env|nohup|xargs|time|nice)\s+)*(?:\S*/)?"
)

# (正则, 拒绝原因)。只收"不可恢复的系统级破坏"——可恢复的操作交给审批闸门。
DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        _CMD_START + r"rm\b[^|;&]*\s(?:/\*?|~/?\*?|\$HOME/?\*?)(?:\s|;|&|$)",
        "删除根目录或家目录",
    ),
    (_CMD_START + r"mkfs(?:\.\w+)?\b", "格式化文件系统"),
    (_CMD_START + r"dd\b[^|;&]*\bof=/dev/", "直接写入块设备"),
    (r">\s*/dev/(?:sd|hd|vd|nvme|mmcblk)\w*", "覆盖块设备"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork 炸弹"),
    (_CMD_START + r"(?:shutdown|reboot|halt|poweroff)\b", "关机或重启系统"),
    (_CMD_START + r"ch(?:mod|own)\s+-R\s+\S+\s+/(?:\s|$)", "递归修改根目录的权限或属主"),
)

# 工具名 → 需要审批的原因。不在这里的工具一律直接放行。
APPROVAL_RULES: dict[str, str] = {
    "bash": "执行 shell 命令",
    "write_file": "写入文件（已有内容会被覆盖）",
    "edit_file": "修改文件内容",
    "subagent": "并行派发 subagent（会额外消耗多次模型调用）",
}

AskUser = Callable[[str, dict[str, Any], str], bool]

# 多个 subagent 并行时可能同时来要审批，而终端只有一个。
#
# 免审批开关本身由 RunState 显式传递，不再用 ContextVar：子 agent 在别的线程跑，
# contextvars 不跨线程继承，隐式状态在那里会静默失效；显式传参则传不过去就报错。
_ASK_LOCK = threading.Lock()


def hard_deny(name: str, arguments: Any) -> str | None:
    """闸门 1：只对 bash 的 command 做黑名单匹配，命中返回拒绝原因。"""
    if name != "bash" or not isinstance(arguments, dict):
        return None

    command = arguments.get("command")
    if not isinstance(command, str):
        return None

    for pattern, reason in DENY_PATTERNS:
        if re.search(pattern, command, re.MULTILINE):
            return reason
    return None


def match_rule(name: str, arguments: dict[str, Any]) -> str | None:
    """闸门 2：命中则返回需要审批的原因；返回 None 表示直接放行。

    arguments 目前不参与判断——内容级规则等有评测数据再补。
    """
    return APPROVAL_RULES.get(name)


def ask_user(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """闸门 3：交互式确认。读不到输入一律拒绝。

    提示写 stderr，避免污染 stdout 上给用户看的最终答复。
    """
    detail = json.dumps(arguments, ensure_ascii=False, default=str)
    # 并行 subagent 会同时来问，终端只有一个——串行化，否则提示会互相穿插。
    with _ASK_LOCK:
        print(
            f"\n⚠ 需要确认：{reason}\n  工具 {name} {detail}\n  允许执行？[y/N] ",
            file=sys.stderr,
            end="",
            flush=True,
        )
        try:
            answer = sys.stdin.readline()
        except (OSError, KeyboardInterrupt):
            return False

    if not answer:
        print("（读不到输入，视为拒绝）", file=sys.stderr)
        return False
    return answer.strip().lower() in {"y", "yes"}


def check_permission(
    name: str,
    arguments: dict[str, Any],
    *,
    ask: AskUser | None = None,
) -> bool:
    """三道闸门依次判断，返回 True 表示允许执行。"""
    reason = hard_deny(name, arguments)
    if reason:
        logger.warning("硬拒绝 %s：%s", name, reason)
        print(f"\n⛔ 已拒绝：{reason}", file=sys.stderr)
        return False

    reason = match_rule(name, arguments)
    if not reason:
        return True

    ask = ask_user if ask is None else ask
    if ask(name, arguments, reason):
        logger.info("审批通过 %s", name)
        return True

    logger.warning("审批拒绝 %s：%s", name, reason)
    return False


def _always_allow(name: str, arguments: dict[str, Any], reason: str) -> bool:
    return True


def auto_approve(name: str, arguments: dict[str, Any]) -> bool:
    """``--yes`` 用：跳过审批闸门，硬拒绝仍然生效。"""
    return check_permission(name, arguments, ask=_always_allow)
