"""Hook 机制：把循环的扩展点从代码里挪到注册表。

四个事件，按循环的阶段排列：

===============  ==========================================
``UserPromptSubmit``  用户输入进入模型之前
``PreToolUse``        每个工具调用执行之前
``PostToolUse``       工具返回之后、结果写回 messages 之前
``Stop``              模型不再请求工具、准备返回之前
===============  ==========================================

调用约定：

* 回调签名 ``hook(context: dict) -> str | None``，返回 ``"block"`` 表示拦截。
* 同一事件的所有回调**按注册顺序全部执行，不短路**——短路会让审计回调漏掉
  被拒的那次调用，而那正是最需要记录的。
* 回调之间靠同一个 ``context`` 字典传数据：前一个回调写入的字段，后一个回调
  与循环本身都能读到。``trigger_hooks`` 会往 context 里写入 ``event``。
* 回调抛异常按 ``"block"`` 处理（失败关闭），日志里带上回调名。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ..policy.permission import (
    DEFAULT_MODE,
    always_allow,
    danger_reason,
    gate,
)

logger = logging.getLogger("avid.runtime.hooks")

# 日志摘要里要打码的键名/写法。INFO 是默认日志级别，而工具参数里经常带凭据：
# `bash` 的 `curl -H "Authorization: Bearer sk-…"`、`write_file` 写 .env 的内容……
# 日志会被收集、转发、贴进 issue，所以"原样打印整份参数"不能接受。
_SECRET_KEYS = ("token", "api_key", "apikey", "authorization", "password", "secret", "key")
# 两种写法都要盖住：`--token=xyz` / `API_KEY: xyz`，以及
# `Authorization: Bearer xyz`（值前面还有一个 scheme 词）。
_SECRET_IN_TEXT = re.compile(
    r"(?i)((?:authorization|token|api[_-]?key|password|secret)\s*[=:]\s*)"
    r"(?:bearer\s+)?\S+"
)
_SECRET_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_BRIEF_LIMIT = 300


def brief(arguments: Any, *, limit: int = _BRIEF_LIMIT) -> str:
    """日志用的参数摘要：敏感键与内联凭据打码，并截断长度。

    策略是"宁可多打一点码"：日志只用来定位问题，不需要完整的命令原文。
    """

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    "***"
                    if any(mark in str(key).lower() for mark in _SECRET_KEYS)
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return _SECRET_IN_TEXT.sub(
                r"\1***", _SECRET_BEARER.sub(r"\1***", value)
            )
        return value

    text = json.dumps(redact(arguments), ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…（已截断）"

# 事件名白名单。register_hook 对表外的事件名直接报错——事件名拼错会让权限
# 校验静默消失，这类错误必须炸出来。
EVENTS: tuple[str, ...] = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

Hook = Callable[[dict[str, Any]], "str | None"]

ALLOW = "allow"
BLOCK = "block"

HOOKS: dict[str, list[Hook]] = {event: [] for event in EVENTS}

# 工具输出进入上下文前的预算上限。比工具自身的截断更严：工具层管
# "单次输出别太大"，这一层管"塞进上下文的别太多"。
MAX_TOOL_OUTPUT_CHARS = 8000


def register_hook(event: str, hook: Hook | None = None):
    """注册回调。既可直接调用，也能当装饰器用。

        register_hook("PreToolUse", my_hook)

        @register_hook("PreToolUse")
        def my_hook(context): ...
    """
    if event not in EVENTS:
        raise ValueError(f"未知事件名 {event!r}；可用：{'、'.join(EVENTS)}")

    if hook is None:
        return lambda fn: register_hook(event, fn)

    HOOKS.setdefault(event, []).append(hook)
    return hook


def trigger_hooks(event: str, context: dict[str, Any]) -> str:
    """触发某事件的全部回调，返回 ALLOW 或 BLOCK。

    ``context`` 会被原地修改；调用方在返回后读其中的字段决定下一步动作。
    """
    if event not in EVENTS:
        raise ValueError(f"未知事件名 {event!r}；可用：{'、'.join(EVENTS)}")

    context["event"] = event
    blocked = False

    for hook in list(HOOKS.get(event, [])):
        name = getattr(hook, "__name__", repr(hook))
        try:
            result = hook(context)
        except Exception:
            logger.exception("回调 %s 在 %s 抛出异常，按 block 处理", name, event)
            blocked = True
            continue
        if result == BLOCK:
            logger.info("%s 被 %s 拦截", event, name)
            blocked = True

    return BLOCK if blocked else ALLOW


# ---------------- 默认回调 ----------------


def context_inject_hook(context: dict[str, Any]) -> str | None:
    """UserPromptSubmit：注入工作区路径与可用工具，省掉模型猜环境的往返。

    工作区根优先取运行级上下文（``state.workspace_root``，阶段 18 起每个会话可以属于
    不同工作区），没有时回落到进程默认根。
    """
    from ..tools import TOOLS, workspace

    root = context.get("workspace_root") or workspace.WORKSPACE_ROOT
    names = "、".join(item["function"]["name"] for item in TOOLS)
    context.setdefault("injected", []).append(
        f"[环境] 工作区根目录：{root}\n[环境] 可用工具：{names}"
    )
    return None


# 需要按"目标是否在工作区之外"判断的工具：它们的 path 参数就是待判定的目标。
PATH_TOOLS = frozenset({"read_file", "write_file", "edit_file", "glob"})


def permission_facts(
    name: str, arguments: dict[str, Any], root: str | None = None
) -> tuple[str | None, str | None]:
    """算出危险类别与越界目标两个**事实**，交给 ``permission.gate`` 裁决。

    路径数学只有一份（``tools/workspace.py``），策略层不复制它；这里也不做决定。
    """
    from pathlib import Path

    from ..tools import workspace

    base = Path(root) if root else None
    danger = danger_reason(name, arguments)

    if name == "bash":
        command = arguments.get("command")
        target = (
            workspace.outside_command_target(command, root=base)
            if isinstance(command, str)
            else None
        )
    elif name in PATH_TOOLS:
        raw = arguments.get("path")
        target = (
            workspace.outside_target(raw, root=base) if isinstance(raw, str) else None
        )
    else:
        target = None

    return danger, target


def permission_hook(context: dict[str, Any]) -> str | None:
    """PreToolUse：走四层权限裁决；拒绝时按档写明原因，并给出可执行的下一步。

    硬拒绝、危险命令、越界、常规规则对模型意味着完全不同的事——"永远不许，换做法"、
    "这条命令危险，换非破坏性做法"、"这个路径在区外，别重复试"、"这次不行，别重复提交"。
    只回一句 "Permission denied." 会让模型分不清，于是反复重试同一条命令直到烧穿轮数上限。

    运行级上下文由 ``execution.execute_one`` 注入：``permission_mode``、``approval_ledger``、
    ``workspace_root``、``ask``、``auto_approve``。审批回调优先取 ``context["ask"]``；
    没有注入时逐字回落到 stdin（CLI 路径），行为与改动前一致（设计文档 §7.2）。
    """
    name = context.get("tool", "")
    arguments = context.get("arguments") or {}
    mode = context.get("permission_mode") or DEFAULT_MODE
    ledger = context.get("approval_ledger")
    danger, outside = permission_facts(name, arguments, context.get("workspace_root"))

    # ``--yes`` 只换回答者，不改变"哪些动作会打问号"。
    answerer = always_allow if context.get("auto_approve") else context.get("ask")

    decision = gate(
        name,
        arguments,
        mode=mode,
        ask=answerer,
        ledger=ledger,
        danger=danger,
        outside=outside,
    )
    if decision.allowed:
        return None

    context["denied_kind"] = decision.kind
    context["denied_reason"] = f"{name}：{decision.reason}"
    context["denied_content"] = decision.message
    return BLOCK


def log_hook(context: dict[str, Any]) -> str | None:
    """PreToolUse + PostToolUse：审计一次调用的开始与结束。永不拦截。"""
    tool = context.get("tool")
    if context.get("event") == "PreToolUse":
        logger.info("[hook] 调用 %s %s", tool, brief(context.get("arguments")))
    else:
        content = context.get("content") or ""
        suffix = "（已被截断）" if context.get("truncated") else ""
        logger.info("[hook] 返回 %s，%d 字符%s", tool, len(content), suffix)

    if context.get("denied_reason"):
        logger.info("[hook] 拒绝原因：%s", context["denied_reason"])
    return None


def large_output_hook(context: dict[str, Any]) -> str | None:
    """PostToolUse：按上下文预算截断工具输出。"""
    content = context.get("content")
    if not isinstance(content, str) or len(content) <= MAX_TOOL_OUTPUT_CHARS:
        return None

    original = len(content)
    notice = (
        f"\n…（hook 按上下文预算截断，原文 {original} 字符，上限 {MAX_TOOL_OUTPUT_CHARS}）"
    )
    # 连提示一起算进预算，否则"上限 8000"实际会超出提示的长度。
    keep = max(0, MAX_TOOL_OUTPUT_CHARS - len(notice))
    context["content"] = content[:keep] + notice
    context["truncated"] = True
    return None


def summary_hook(context: dict[str, Any]) -> str | None:
    """Stop：把本次运行的轮数与工具使用情况汇总进日志。

    当前不阻止退出；Stop 的拦截能力留给需要"再补一轮"的场景（测试里有用例）。
    """
    summary = (
        f"轮数={context.get('rounds', 0)} "
        f"工具调用={context.get('tool_calls', 0)} "
        f"拒绝={context.get('denials', 0)}"
    )
    context["summary"] = summary
    logger.info("[hook] 运行汇总：%s", summary)
    return None


# 注册顺序有意义：permission_hook 先跑，log_hook 才能看到 denied_reason；
# large_output_hook 先跑，log_hook 才能报出"已被截断"。
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("PostToolUse", log_hook)
register_hook("Stop", summary_hook)
