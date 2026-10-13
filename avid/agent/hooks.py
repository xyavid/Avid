"""The hook surface: extra configuration on the loop's four events, declared in
``<workspace>/.avid/hooks.json`` as scripts.

The body registers nothing here — the permission gate, the output budget, the repeat reminder and
the run summary are plain code in guards.py — so what a run enforces never depends on what someone
put in this registry. A script decides and may inject text; it never rewrites a tool call or its
result, and every way it can fail blocks the step.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("avid.agent.hooks")

# The four hook event names; anything else raises, because a typo would silently disable a gate.
EVENTS: tuple[str, ...] = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

# Callback signature: return "block" to veto the step, or None to let it pass.
Hook = Callable[[dict[str, Any]], "str | None"]

ALLOW = "allow"
BLOCK = "block"

#: Declaration file, beside the MCP one; a workspace without it configures no hooks at all.
HOOKS_SUBDIR = Path(".avid") / "hooks.json"
# A hook runs inside a tool call's own latency budget, so it gets seconds, not minutes.
DEFAULT_HOOK_TIMEOUT_SECONDS = 5.0

#: What each event may send to a script. An event's context also carries kernel objects (the
#: security spec, the approval callback), which must never reach a subprocess.
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "UserPromptSubmit": (
        "prompt",
        "messages",
        "workspace_root",
        "permission_mode",
        "tool_names",
    ),
    "PreToolUse": ("tool", "arguments", "round", "tool_call_id", "workspace_root"),
    "PostToolUse": (
        "tool",
        "arguments",
        "round",
        "tool_call_id",
        "content",
        "truncated",
        "workspace_root",
    ),
    "Stop": (
        "final_text",
        "messages",
        "rounds",
        "tool_calls",
        "denials",
        "denial_streak",
        "compactions",
    ),
}


class HookRegistry:
    """Callbacks for one run; isolating them lets a test or subagent replace the whole set."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {event: [] for event in EVENTS}

    def register(self, event: str, hook: Hook | None = None):
        """Register a callback, or act as a decorator; an unknown event name raises."""
        if event not in EVENTS:
            raise ValueError(f"未知事件名 {event!r}；可用：{'、'.join(EVENTS)}")
        if hook is None:
            return lambda fn: self.register(event, fn)
        self._hooks.setdefault(event, []).append(hook)
        return hook

    def registered(self, event: str) -> list[Hook]:
        """Return the callbacks for one event in registration order, for diagnostics and tests."""
        return list(self._hooks.get(event, []))

    def trigger(self, event: str, context: dict[str, Any]) -> str:
        """Run every callback for an event in order and report whether any of them blocked."""
        if event not in EVENTS:
            raise ValueError(f"未知事件名 {event!r}；可用：{'、'.join(EVENTS)}")

        # The event name travels in the context so one callback can serve both phases.
        context["event"] = event
        blocked = False

        # Every callback runs even after one blocks, so an audit callback still sees the call.
        for hook in list(self._hooks.get(event, [])):
            name = getattr(hook, "__name__", repr(hook))
            try:
                result = hook(context)
            except Exception:
                # A raising callback counts as a block, so a broken gate fails closed.
                logger.exception("回调 %s 在 %s 抛出异常，按 block 处理", name, event)
                blocked = True
                continue
            if result == BLOCK:
                logger.info("%s 被 %s 拦截", event, name)
                blocked = True

        return BLOCK if blocked else ALLOW

    def copy(self) -> "HookRegistry":
        """Return an independently extendable copy; the callback objects themselves stay shared."""
        clone = HookRegistry()
        for event, callbacks in self._hooks.items():
            clone._hooks[event] = list(callbacks)
        return clone


@dataclass(frozen=True)
class HookScript:
    """One declared script: the event it answers and how to run it."""

    event: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS


def hooks_config_path(workspace_root: str | None) -> Path:
    """``<workspace root>/.avid/hooks.json``, or under the process cwd without a root."""
    base = Path(workspace_root) if workspace_root is not None else Path.cwd()
    return base / HOOKS_SUBDIR


def _script_of(entry: dict[str, Any], index: int, warnings: list[str]) -> HookScript | None:
    event = str(entry.get("event", "")).strip()
    command = str(entry.get("command", "")).strip()
    if event not in EVENTS:
        warnings.append(
            f"hooks.json 第 {index} 项的 event {event!r} 不是四个事件之一，已跳过"
        )
        return None
    if not command:
        warnings.append(f"hooks.json 第 {index} 项缺少 command，已跳过")
        return None
    args = entry.get("args")
    env = entry.get("env")
    try:
        timeout = float(entry.get("timeout_seconds", DEFAULT_HOOK_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        warnings.append(f"hooks.json 第 {index} 项的 timeout_seconds 不是数字，用默认值")
        timeout = DEFAULT_HOOK_TIMEOUT_SECONDS
    return HookScript(
        event=event,
        command=command,
        args=tuple(str(item) for item in args) if isinstance(args, list) else (),
        env=({str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}),
        timeout=timeout if timeout > 0 else DEFAULT_HOOK_TIMEOUT_SECONDS,
    )


def read_config(workspace_root: str | None) -> tuple[list[HookScript], list[str]]:
    """Parse the declaration file; a missing file is not a warning, a broken entry is skipped."""
    path = hooks_config_path(workspace_root)
    if not path.is_file():
        return [], []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"hooks.json 无法解析，已跳过全部钩子：{exc}"]
    entries = payload.get("hooks") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return [], [f"hooks.json 缺少 hooks 数组，已跳过：{path}"]

    specs: list[HookScript] = []
    warnings: list[str] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            warnings.append(f"hooks.json 第 {index} 项不是对象，已跳过")
            continue
        spec = _script_of(entry, index, warnings)
        if spec is not None:
            specs.append(spec)
    return specs, warnings


def _payload(event: str, context: dict[str, Any]) -> dict[str, Any]:
    """The declared fields only, so a subprocess never sees the context's kernel objects."""
    body = {name: context.get(name) for name in EVENT_FIELDS[event] if name in context}
    body["event"] = event
    return body


def _run_script(spec: HookScript, payload: dict[str, Any]) -> tuple[bool, str, str]:
    """Run one script and read its verdict; returns (blocked, reason, injected).

    Every failure mode blocks: a hook that cannot answer must not read as an approval.
    """
    try:
        done = subprocess.run(
            [spec.command, *spec.args],
            input=json.dumps(payload, ensure_ascii=False, default=str),
            capture_output=True,
            text=True,
            timeout=spec.timeout,
            env={**os.environ, **spec.env},
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return True, f"钩子脚本无法执行：{exc}", ""

    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        tail = detail[-1][:200] if detail else ""
        return True, f"钩子脚本退出码 {done.returncode}：{tail}", ""

    try:
        answer = json.loads(done.stdout or "{}")
    except ValueError:
        return True, "钩子脚本没有回一个 JSON 结果", ""
    if not isinstance(answer, dict):
        return True, "钩子脚本的结果不是 JSON 对象", ""

    decision = str(answer.get("decision", "allow")).strip().lower()
    if decision not in (ALLOW, BLOCK):
        return True, f"钩子脚本的 decision 只能是 allow 或 block，收到 {decision!r}", ""
    return (
        decision == BLOCK,
        str(answer.get("reason") or "").strip(),
        str(answer.get("injected") or "").strip(),
    )


def _script_callback(spec: HookScript) -> Hook:
    """Adapt one declared script to the registry's calling convention."""

    def callback(context: dict[str, Any]) -> str | None:
        blocked, reason, injected = _run_script(spec, _payload(spec.event, context))
        if injected:
            if spec.event == "UserPromptSubmit":
                context.setdefault("injected", []).append(injected)
            else:
                logger.warning(
                    "钩子 %s 在 %s 上回了 injected；只有 UserPromptSubmit 用得上，已忽略",
                    spec.command,
                    spec.event,
                )
        if not blocked:
            return None
        logger.info("钩子 %s 拦截了 %s：%s", spec.command, spec.event, reason or "（无理由）")
        context["denied_reason"] = reason or f"{spec.command} 拦截了这次调用"
        # The reason is what the model reads, so a hook can explain itself in its own words.
        context["denied_content"] = reason
        if spec.event == "Stop":
            context["nudge"] = reason or None
        return BLOCK

    callback.__name__ = f"script:{spec.command}"
    return callback


def load_hooks(workspace_root: str | None) -> HookRegistry:
    """This run's registry from the workspace declaration; every skip is logged where it happens."""
    registry = HookRegistry()
    specs, warnings = read_config(workspace_root)
    for warning in warnings:
        logger.warning("%s", warning)
    for spec in specs:
        registry.register(spec.event, _script_callback(spec))
    return registry
