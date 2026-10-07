"""Turns one tool call's arguments into the normalized action facts the engine decides on."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .command_parse import parse_shell

# Command-start anchor: line start or after ; & |, skipping wrapper commands and any path prefix.
# Anchoring keeps a keyword used as an argument (`grep halt file`) from reading as a command, and a
# variable assignment inserted before the program still bypasses the blacklist, so it is a guardrail.
_CMD_START = (
    r"(?:^|[;&|]\s*)(?:(?:sudo|command|env|nohup|xargs|time|nice)\s+)*(?:\S*/)?"
)

# 毁灭级：不可恢复的系统级破坏。默认形态下它触发一次询问（二次确认在 UI 层），full 由
# 显式授权跳过——它不再等于「任何模式都拒」的硬拒，唯一硬拒只剩凭据拒读。
DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        _CMD_START + r"rm\b[^|;&]*\s(?:/\*?|~/?\*?|\$HOME/?\*?)(?:\s|;|&|$)",
        "删除根目录或家目录",
    ),
    (
        _CMD_START + r"mkfs(?:\.\w+)?\b|(?:format-volume|clear-disk|initialize-disk)\b",
        "格式化文件系统",
    ),
    (_CMD_START + r"dd\b[^|;&]*\bof=/dev/", "直接写入块设备"),
    (r">\s*/dev/(?:sd|hd|vd|nvme|mmcblk)\w*", "覆盖块设备"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork 炸弹"),
    (_CMD_START + r"(?:shutdown|reboot|halt|poweroff|stop-computer|restart-computer)\b", "关机或重启系统"),
    (_CMD_START + r"ch(?:mod|own)\s+-R\s+\S+\s+/(?:\s|$)", "递归修改根目录的权限或属主"),
)

# Dangerous commands: side effects outside the workspace, irreversible data loss, or privilege gain.
# Whether a danger needs approval is decided by the three axes, not by this table.
DANGER_PATTERNS: tuple[tuple[str, str], ...] = (
    (_CMD_START + r"(?:sudo|su|doas|pkexec)\b", "提权"),
    (
        # Long options must be recognized too: matching only -[a-zA-Z]*[rRf] would let
        # `rm --recursive x` skip the danger tier and, under the sandbox, run without asking.
        _CMD_START + r"rm\b[^|;&]*\s(?:--(?:recursive|force|dir)\b|-[a-zA-Z]*[rRf])",
        "递归或强制删除",
    ),
    (_CMD_START + r"ch(?:mod|own|grp)\b", "权限或属主变更"),
    (_CMD_START + r"(?:dd|fdisk|parted|mount|umount|losetup|swapon|swapoff|truncate)\b", "磁盘或文件系统操作"),
    (_CMD_START + r"(?:systemctl|service|kill|pkill|killall|systemd-run)\b", "系统服务或进程操作"),
    (_CMD_START + r"(?:crontab|at)\b", "计划任务"),
    (_CMD_START + r"(?:apt|apt-get|dpkg|dnf|yum|pacman|snap|brew|zypper|apk)\b", "系统级包管理"),
    (
        r"(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|python\d?)\b",
        "把网络内容直接交给解释器执行",
    ),
    (
        r"\b(?:sh|bash|zsh|python\d?|source)\b[^|;&]*<\s*\(\s*(?:curl|wget)\b",
        "把网络内容直接交给解释器执行",
    ),
    (_CMD_START + r"git\b[^|;&]*\bpush\b[^|;&]*(?:--force|-f)\b", "强制推送"),
    (_CMD_START + r"git\b[^|;&]*\breset\b[^|;&]*--hard\b", "丢弃工作区改动"),
    (
        _CMD_START + r"git\b[^|;&]*\bclean\b[^|;&]*(?:\s-[a-zA-Z]*f|--force)",
        "删除未跟踪文件",
    ),
    (_CMD_START + r"find\b[^|;&]*\s-delete\b", "批量删除文件"),
    (_CMD_START + r"(?:ssh|scp|rsync)\b", "远程访问或传输"),
    (_CMD_START + r"(?:docker|podman|kubectl|helm)\b", "容器或编排操作"),
    # PowerShell 动词：与 POSIX 同类目同档（表按命令文本匹配，PS 命令不会出现在
    # bash 的正常用法里，反之亦然，合一张表没有误伤）。
    (
        _CMD_START + r"remove-item\b[^|;&]*(?:-recurse\b|-force\b)",
        "递归或强制删除",
    ),
    (_CMD_START + r"(?:invoke-expression|iex)\b", "动态执行代码"),
    (_CMD_START + r"(?:start-process|invoke-command|start-job)\b", "派生进程或远程执行"),
    (_CMD_START + r"(?:stop-process|stop-service|set-service)\b", "进程或服务操作"),
    (_CMD_START + r"set-executionpolicy\b", "更改执行策略"),
)

# Network commands: the network is a first-class boundary, so reaching it is recorded for the audit.
NETWORK_HINTS: tuple[str, ...] = (
    r"(?:^|[;&|]\s*)(?:(?:sudo|command|env|nohup|xargs|time|nice)\s+)*(?:\S*/)?"
    r"(?:curl|wget|ssh|scp|sftp|rsync|nc|ncat|telnet|ftp)\b",
    r"\bgit\b[^|;&]*\b(?:push|fetch|pull|clone|ls-remote)\b",
    r"\b(?:pip|pip3|uv|npm|pnpm|yarn|cargo|go)\b[^|;&]*\b(?:install|add|get|update|publish)\b",
)

# Clue for "does this command write"; bash is then checked against read and write deny rules alike,
# because one extra question is cheaper than missing a write prohibition.
_WRITE_HINTS = re.compile(
    r"(?:>>?\s*\S|\btee\b|\bcp\b|\bmv\b|\brm\b|\brmdir\b|\bmkdir\b|\btouch\b|\bln\b"
    r"|\binstall\b|\btruncate\b|\bdd\b|\bsed\s+-i\b|\bpatch\b|\bunzip\b|\btar\b[^|;&]*-x"
    r"|\bchmod\b|\bchown\b|\bgit\s+(?:add|commit|checkout|reset|clean|apply|stash)\b)",
    re.MULTILINE,
)

# Credentials, cloud keys, private keys and shadow passwords: reading or writing them is dangerous.
#
# Matching goes by expanded path component rather than by a resolved $HOME prefix, because dot
# directories under $HOME are often symlinks (in WSL `~/.aws` points at a Windows path), and a
# resolve() would then no longer match the prefix.
SENSITIVE_COMPONENTS: tuple[str, ...] = (".ssh", ".aws", ".gnupg", ".docker")
SENSITIVE_ABSOLUTE: tuple[str, ...] = (
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/root",
)
SENSITIVE_SUFFIX = ".pem"

# Commands are split on whitespace and shell metacharacters, then each token is tested as a path.
_SENSITIVE_SPLIT = re.compile(r"[\s;|&()<>'\"]+")

# File tools whose target is their path argument.
PATH_TOOLS: frozenset[str] = frozenset({"read_file", "write_file", "edit_file", "glob"})

# Write-class file tools; membership decides whether read or write rules are consulted.
WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})

# Operation names shared with the file tools' 只读/写 判定。
OPERATION_READ = "read"
OPERATION_WRITE = "write"

# Risk marker for "a target lies outside the workspace"; it records position, not danger —
# 阶段 51 起区外读写都直接执行并自动挂载，这个标记只进审计与展示。
OUTSIDE_RISK = "越界"

# Capabilities that change state outside the sandbox; reading the host is already granted.
WRITE_CAPABILITIES: frozenset[str] = frozenset({"filesystem_write", "filesystem_delete"})


def _outside_write_targets(command: str, outside: tuple[str, ...], root: str | None) -> tuple[str, ...]:
    """Narrows write targets to known shell shapes; an uncertain command keeps every target.

    Approval only, not isolation: unknown commands are still blocked by the mounts.
    """
    # Imported here rather than at module level to avoid a policy <-> tools import cycle.
    from ..agent.tools import workspace

    facts = parse_shell(command)
    if facts.uncertain:
        return outside
    writes: set[str] = set()
    for segment in facts.segments:
        if not segment:
            continue
        words = list(segment)
        program = Path(words[0]).name
        redirect_targets: list[str] = []
        for index, word in enumerate(words):
            if word in {">", ">>", "<>"}:
                if index + 1 >= len(words):
                    return outside
                redirect_targets.append(str(workspace.target_path(words[index + 1], root=Path(root) if root else None)))
        if program in {"cp", "mv", "install", "ln"}:
            # Only option-free source and destination are recognized; options keep the targets.
            operands = [word for word in words[1:] if word not in {">", ">>", "<>"}]
            if len(operands) < 2 or any(word.startswith("-") for word in operands):
                return outside
            writes.add(str(workspace.target_path(operands[-1], root=Path(root) if root else None)))
        elif program in {"cat", "echo", "printf", "rg", "grep", "head", "tail", "wc"}:
            # These commands only read, so any write has to come from a redirection.
            pass
        else:
            # A non-whitelisted command may write any argument, so redirects cannot prove reads.
            writes.update(outside)
        writes.update(redirect_targets)
    return tuple(target for target in outside if target in writes)


def _under(path: Path, base: Path) -> bool:
    """Reports whether ``path`` is inside ``base``, ``base`` included."""
    return path == base or base in path.parents


def sensitive_reason(raw: str) -> str | None:
    """Returns a category name when a path literal is a sensitive target, else ``None``.

    ``~`` and ``$HOME`` are expanded first, then path components and absolute targets are checked.
    """
    text = os.path.expanduser(os.path.expandvars(raw.strip()))
    if not text:
        return None
    if text.endswith(SENSITIVE_SUFFIX):
        return "敏感路径"

    candidate = Path(text)
    if any(part in SENSITIVE_COMPONENTS for part in candidate.parts):
        return "敏感路径"

    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        resolved = candidate
    for entry in SENSITIVE_ABSOLUTE:
        if _under(resolved, Path(entry)) or _under(candidate, Path(entry)):
            return "敏感路径"
    return None


def _sensitive_in_command(command: str) -> str | None:
    for token in _SENSITIVE_SPLIT.split(command):
        if sensitive_reason(token):
            return "敏感路径"
    return None


def command_key(command: str) -> str:
    """Returns the ledger key for dangerous and outside commands: whitespace-normalized text."""
    return " ".join(command.split())


def normalize_command(command: str) -> str:
    """Folds whitespace in a command; the single rule for two spellings of the same thing."""
    return command_key(command)


def hard_deny(name: str, arguments: Any) -> str | None:
    """Returns a deny reason for a bash command matching the hard-deny blacklist, else ``None``."""
    if name != "bash" or not isinstance(arguments, dict):
        return None

    command = arguments.get("command")
    if not isinstance(command, str):
        return None

    for pattern, reason in DENY_PATTERNS:
        if re.search(pattern, command, re.MULTILINE):
            return reason
    # Every parsed segment is re-tested so a blacklisted command after a separator is not missed.
    facts = parse_shell(command)
    for segment in facts.segments:
        candidate = " ".join(segment)
        for pattern, reason in DENY_PATTERNS:
            if re.search(pattern, candidate, re.MULTILINE):
                return reason
    return None


def danger_categories(name: str, arguments: Any) -> tuple[str, ...]:
    """Returns the danger category names in table order; an empty tuple means not dangerous."""
    if not isinstance(arguments, dict):
        return ()

    found: list[str] = []
    if name != "bash":
        # No other tool is pattern-matched; at most its path can be sensitive.
        path = arguments.get("path")
        if isinstance(path, str) and sensitive_reason(path):
            found.append("敏感路径")
        return tuple(found)

    command = arguments.get("command")
    if not isinstance(command, str):
        return ()
    if _sensitive_in_command(command):
        found.append("敏感路径")
    for pattern, category in DANGER_PATTERNS:
        if category in found:
            continue
        if re.search(pattern, command, re.MULTILINE):
            found.append(category)
    facts = parse_shell(command)
    for segment in facts.segments:
        candidate = " ".join(segment)
        for pattern, category in DANGER_PATTERNS:
            if category not in found and re.search(pattern, candidate, re.MULTILINE):
                found.append(category)
    # Capability facts add categories that the raw command text alone would not show.
    if "shell_execute" in facts.capabilities and "解释器执行" not in found:
        found.append("解释器执行")
    if "filesystem_delete" in facts.capabilities and "删除文件" not in found:
        found.append("删除文件")
    if "external_side_effect" in facts.capabilities and "对外副作用" not in found:
        found.append("对外副作用")
    if facts.uncertain and "无法证明安全的 shell 结构" not in found:
        found.append("无法证明安全的 shell 结构")
    return tuple(found)


def danger_reason(name: str, arguments: Any) -> str | None:
    """Returns the first danger category name, or ``None`` when the call is not dangerous."""
    categories = danger_categories(name, arguments)
    return categories[0] if categories else None


def reaches_network(name: str, arguments: Any) -> bool:
    """Reports whether the call explicitly reaches the network."""
    if name != "bash" or not isinstance(arguments, dict):
        return False
    command = arguments.get("command")
    if not isinstance(command, str):
        return False
    return "network_connect" in parse_shell(command).capabilities or any(
        re.search(pattern, command, re.MULTILINE) for pattern in NETWORK_HINTS
    )


def network_target(command: str) -> str:
    """Extracts the destination of a network command; an unknown one is not treated as safe."""
    url = re.search(r"https?://[^\s'\"|;&()<>]+", command)
    if url:
        return urlsplit(url.group(0)).hostname or "unknown"
    # ssh and scp targets rarely carry a URL, so the raw argument is kept for audit, not resolved.
    host = re.search(r"\b(?:ssh|scp)\s+(?:-[\w-]+\s+)*([^\s;|&]+)", command)
    return host.group(1) if host else "unknown"


def _operations_for(name: str, command: str | None) -> tuple[str, ...]:
    if name in WRITE_TOOLS:
        return (OPERATION_WRITE,)
    if name in PATH_TOOLS:
        return (OPERATION_READ,)
    if name == "bash" and isinstance(command, str):
        # bash counts as both operations so a write deny cannot be missed by a read/write guess.
        if _WRITE_HINTS.search(command):
            return (OPERATION_WRITE, OPERATION_READ)
        return (OPERATION_READ, OPERATION_WRITE)
    return ()


def _scan_paths(
    name: str, arguments: dict[str, Any], root: str | None
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Returns ``(all, outside, sensitive)`` absolute targets for one call.

    File tool targets come from the exact path math; bash targets are a literal-token scan.
    """
    # Imported here rather than at module level to avoid a policy <-> tools import cycle.
    from ..agent.tools import workspace

    base = Path(root) if root else None
    targets: list[Path] = []

    if name == "bash":
        command = arguments.get("command")
        if isinstance(command, str):
            targets = list(workspace.command_targets(command, root=base))
    elif name in PATH_TOOLS:
        raw = arguments.get("path")
        if isinstance(raw, str) and raw.strip():
            targets = [workspace.target_path(raw, root=base)]

    resolved = tuple(str(path) for path in targets)
    outside = tuple(
        str(path) for path in targets if base is not None and not workspace.is_within(path, base.resolve())
    )
    credentials = tuple(
        str(path) for path in targets if sensitive_reason(str(path))
    )
    return resolved, outside, credentials


@dataclass(frozen=True)
class Action:
    """Normalized facts about one tool call; it carries no verdict, the engine owns that."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    normalized: str = ""
    targets: tuple[str, ...] = ()
    outside: tuple[str, ...] = ()
    outside_writes: tuple[str, ...] = ()
    credentials: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    damage: str | None = None
    network: bool = False
    network_target: str = ""
    capabilities: frozenset[str] = frozenset()
    operations: tuple[str, ...] = ()
    workspace_root: str | None = None

    @property
    def danger(self) -> str | None:
        """Returns the first danger category name, for wording."""
        return self.risks[0] if self.risks else None

    def ledger_key(self) -> tuple[str, str]:
        """Returns the ledger key: normalized command, else the first target, else the arguments.

        The key kind is the capability kind, so an approval grants one command or one path.
        """
        command = self.arguments.get("command")
        if isinstance(command, str) and command.strip():
            return ("command", normalize_command(command))
        if self.targets:
            return ("path", self.targets[0])
        path = self.arguments.get("path")
        if isinstance(path, str) and path.strip():
            return ("path", path.strip())
        return ("args", json.dumps(self.arguments, ensure_ascii=False, sort_keys=True, default=str))


def brokerize(name: str, arguments: Any, *, root: str | None = None) -> Action:
    """Builds the ``Action`` for one tool call; the outside fact is recorded but not filtered."""
    args = arguments if isinstance(arguments, dict) else {}
    command = args.get("command") if isinstance(args.get("command"), str) else None

    targets, outside, credentials = _scan_paths(name, args, root)
    outside_writes = (
        _outside_write_targets(command, outside, root) if name == "bash" and command
        else outside if name in WRITE_TOOLS else ()
    )
    # The outside fact is always recorded, even where nothing is enforced: the engine decides that.
    risks = list(danger_categories(name, args))
    if outside and OUTSIDE_RISK not in risks:
        risks.append(OUTSIDE_RISK)
    normalized = normalize_command(command) if command else ""
    if name == "bash" and command:
        caps = set(parse_shell(command).capabilities)
    else:
        caps = {"filesystem_write"} if name in WRITE_TOOLS else {"filesystem_read"} if name in PATH_TOOLS else set()
    if credentials:
        caps.add("credential_access")
    if any(Path(target).name == ".env" or Path(target).name.startswith(".env.") for target in targets):
        caps.add("secret_access")

    return Action(
        tool=name,
        arguments=args,
        normalized=normalized,
        targets=targets,
        outside=outside,
        outside_writes=outside_writes,
        credentials=credentials,
        risks=tuple(risks),
        damage=hard_deny(name, args),
        network=reaches_network(name, args),
        network_target=network_target(command) if command else "",
        capabilities=frozenset(caps),
        operations=_operations_for(name, command),
        workspace_root=root,
    )



# MCP tool names are built as mcp__<server>__<tool>; the gate must know that prefix because an
# external tool's semantics cannot be classified statically, so its handling differs by design.

MCP_TOOL_PREFIX = "mcp__"


def is_mcp_tool(name: str) -> bool:
    """Reports whether a tool is provided dynamically by an MCP server."""
    return name.startswith(MCP_TOOL_PREFIX)


def mcp_server(name: str) -> str:
    """Extracts the server name from an ``mcp__<server>__<tool>`` name."""
    rest = name[len(MCP_TOOL_PREFIX) :]
    return rest.split("__", 1)[0]


__all__ = [
            "DANGER_PATTERNS",
    "DENY_PATTERNS",
    "NETWORK_HINTS",
    "OPERATION_READ",
    "OPERATION_WRITE",
    "OUTSIDE_RISK",
    "PATH_TOOLS",
    "MCP_TOOL_PREFIX",
        "SENSITIVE_ABSOLUTE",
    "SENSITIVE_COMPONENTS",
    "SENSITIVE_SUFFIX",
    "WRITE_CAPABILITIES",
    "WRITE_TOOLS",
    "Action",
    "brokerize",
    "command_key",
    "danger_categories",
    "danger_reason",
        "hard_deny",
        "is_mcp_tool",
    "mcp_server",
    "normalize_command",
    "reaches_network",
    "sensitive_reason",
]
