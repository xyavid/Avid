"""Tool Broker：把模型给的 JSON 参数变成一份**归一化的动作事实**。

流水线上的第一站，也是唯一做"风险分类"的地方（原则②：边界在 LLM 之外，所以这一站
的输出是纯数据，不含任何裁决）：

::

    tool_call{name, arguments}  ──brokerize──▶  Action
                                                 ├ normalize  归一化后的可读形式
                                                 ├ identify   目标路径（绝对）
                                                 ├ classify   风险类别 + 是否出网
                                                 └ operations 读还是写（bash 两者皆是）

裁决者在 ``engine.py``：同一份 ``Action`` 喂给四级 deny 阶梯与三轴决策表，因此
"什么算危险"与"什么算越界"都只有一份定义。

**越界与敏感的判定不是沙箱**：bash 的目标识别是**启发式**（扫字面量记号），变量展开、
``bash script.sh``、解释器内构造的路径都绕得过去。它的用途是决定"要不要问/要不要拒"，
真正拦住 bash 的是 ``sandbox.py`` 的挂载与网络命名空间。

路径数学只有一份，住在 ``tools/workspace.py``；本模块在**函数内**惰性取用它，因为
``tools/`` 里也有模块 import ``policy.permission``（``tools/subagent.py``），模块级
import 会构成 ``policy ↔ tools`` 的包级环。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .modes import APPROVAL_NONE

# 命令起始位置：行首，或 ; & | 之后；跳过程序路径前缀与常见包装命令。
# 用它锚定，避免 `grep halt file` 这类把关键字当参数的误伤。
# 注意：env FOO=1 dd ... 这种插了变量赋值的写法仍能绕过——黑名单只是护栏。
_CMD_START = (
    r"(?:^|[;&|]\s*)(?:(?:sudo|command|env|nohup|xargs|time|nice)\s+)*(?:\S*/)?"
)

# (正则, 拒绝原因)。只收"不可恢复的系统级破坏"——可恢复的操作交给审批闸门。
#: 硬拒绝：任何模式、任何回答都不放行（原则③的 ADMIN DENY 里最硬的一档）。
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

# 危险命令：会在工作区之外产生副作用、不可逆地丢数据、或取得更高权限。
# 与模式无关的部分只有"它是不是危险"；"危险之后怎么办"由三轴的 approval 决定
# （manual 问人 / auto 由分类器裁决 / full 直接放行）。
DANGER_PATTERNS: tuple[tuple[str, str], ...] = (
    (_CMD_START + r"(?:sudo|su|doas|pkexec)\b", "提权"),
    (
        # 短选项组合（-r / -f / -rf / -Rf）与**长选项**（--recursive / --force / --dir）
        # 都要认：只匹配 `-[a-zA-Z]*[rRf]` 时 `rm --recursive x` 会整个漏过危险层，
        # 在沙箱模式下**完全不问**就放行。
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
)

#: 出网命令：网络是一级边界（原则⑤），所以"这次动作碰不碰网"要单独记进审计。
NETWORK_HINTS: tuple[str, ...] = (
    r"(?:^|[;&|]\s*)(?:(?:sudo|command|env|nohup|xargs|time|nice)\s+)*(?:\S*/)?"
    r"(?:curl|wget|ssh|scp|sftp|rsync|nc|ncat|telnet|ftp)\b",
    r"\bgit\b[^|;&]*\b(?:push|fetch|pull|clone|ls-remote)\b",
    r"\b(?:pip|pip3|uv|npm|pnpm|yarn|cargo|go)\b[^|;&]*\b(?:install|add|get|update|publish)\b",
)

#: 判"这条命令是不是在写"的线索。bash 归到写就按写+读两个口径都查 deny 规则——
#: 宁可多问一次，也不要因为读/写分不清而漏掉一条写禁令。
_WRITE_HINTS = re.compile(
    r"(?:>>?\s*\S|\btee\b|\bcp\b|\bmv\b|\brm\b|\brmdir\b|\bmkdir\b|\btouch\b|\bln\b"
    r"|\binstall\b|\btruncate\b|\bdd\b|\bsed\s+-i\b|\bpatch\b|\bunzip\b|\btar\b[^|;&]*-x"
    r"|\bchmod\b|\bchown\b|\bgit\s+(?:add|commit|checkout|reset|clean|apply|stash)\b)",
    re.MULTILINE,
)

# 敏感路径（提权凭据、云密钥、私钥、影子口令）：读取或写入都算危险。
#
# 判定用"展开 + 路径分量"而不是正则字面量：`~/.ssh/config`、`$HOME/.ssh/config`、
# `/home/u/.ssh/config` 是同一个目标，只看字面量会漏掉后两种。
#
# 分量判定（而不是"解析后与 $HOME 比前缀"）是刻意的：$HOME 下的点目录常是符号链接
# （WSL 里 `~/.aws -> /mnt/c/Users/...`），resolve() 之后就不再以 $HOME 为前缀，
# 前缀比较会漏掉它。只要路径分量里出现这些点目录就判敏感——宁可多问一次。
SENSITIVE_COMPONENTS: tuple[str, ...] = (".ssh", ".aws", ".gnupg", ".docker")
SENSITIVE_ABSOLUTE: tuple[str, ...] = (
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/root",
)
SENSITIVE_SUFFIX = ".pem"

# 命令里按空白与 shell 元字符切开后再逐个判路径（与 tools/workspace.py 同一套切法）。
_SENSITIVE_SPLIT = re.compile(r"[\s;|&()<>'\"]+")

#: 需要审批的原因文案：**沙箱不能保证**的那些工具。沙箱可用时它们由挂载保证，
#: 不进这张表；沙箱不可用时（降级）manual 会按这里逐个问人。
APPROVAL_RULES: dict[str, str] = {
    "bash": "执行 shell 命令",
    "write_file": "写入文件（已有内容会被覆盖）",
    "edit_file": "修改文件内容",
}

#: 成本规则（非安全）：沙箱保证不了"要花多少钱"。manual 问一次并记账。
COST_RULES: dict[str, str] = {
    "subagent": "并行派发 subagent（会额外消耗多次模型调用）",
}

#: 文件工具的目标就是它的 path 参数。
PATH_TOOLS: frozenset[str] = frozenset({"read_file", "write_file", "edit_file", "glob"})

#: 写类文件工具（决定查读规则还是写规则）。
WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})

OPERATION_READ = "read"
OPERATION_WRITE = "write"


def _under(path: Path, base: Path) -> bool:
    """path 是否在 base 之内（含 base 本身）。策略层不 import tools，自己写一份。"""
    return path == base or base in path.parents


def sensitive_reason(raw: str) -> str | None:
    """一个路径字面量是不是敏感目标；是则返回类别名。

    先展开 ``~`` 与 ``$HOME``；路径分量里出现敏感点目录（``.ssh`` / ``.aws`` /
    ``.gnupg`` / ``.docker``）即命中，另外覆盖 ``/etc/shadow`` 一类绝对目标与
    ``.pem`` 后缀。
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
    """危险命令与越界命令的记账键：规范化空白后逐字比较。"""
    return " ".join(command.split())


def normalize_command(command: str) -> str:
    """参数归一化：折叠空白。这是"同一件事的两种写法"的**唯一**认定口径。"""
    return command_key(command)


def hard_deny(name: str, arguments: Any) -> str | None:
    """ADMIN 档：只对 bash 的 command 做黑名单匹配，命中返回拒绝原因。"""
    if name != "bash" or not isinstance(arguments, dict):
        return None

    command = arguments.get("command")
    if not isinstance(command, str):
        return None

    for pattern, reason in DENY_PATTERNS:
        if re.search(pattern, command, re.MULTILINE):
            return reason
    return None


def danger_categories(name: str, arguments: Any) -> tuple[str, ...]:
    """危险类别（可能多个，按表的顺序）。空元组表示不是危险命令/敏感目标。"""
    if not isinstance(arguments, dict):
        return ()

    found: list[str] = []
    if name != "bash":
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
    return tuple(found)


def danger_reason(name: str, arguments: Any) -> str | None:
    """第一个危险类别名；``None`` 表示不危险。保留给既有调用方与文案。"""
    categories = danger_categories(name, arguments)
    return categories[0] if categories else None


def reaches_network(name: str, arguments: Any) -> bool:
    """这次动作是否显式出网（curl/wget/ssh/git push/包管理器联网动作）。"""
    if name != "bash" or not isinstance(arguments, dict):
        return False
    command = arguments.get("command")
    if not isinstance(command, str):
        return False
    return any(re.search(pattern, command, re.MULTILINE) for pattern in NETWORK_HINTS)


def _operations_for(name: str, command: str | None) -> tuple[str, ...]:
    """读还是写。bash 分不清时按**两个口径都查**（宁可多问一次）。"""
    if name in WRITE_TOOLS:
        return (OPERATION_WRITE,)
    if name in PATH_TOOLS:
        return (OPERATION_READ,)
    if name == "bash" and isinstance(command, str):
        if _WRITE_HINTS.search(command):
            return (OPERATION_WRITE, OPERATION_READ)
        return (OPERATION_READ, OPERATION_WRITE)
    return ()


def _scan_paths(
    name: str, arguments: dict[str, Any], root: str | None
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """识别目标：返回 (全部目标, 区外目标, 敏感目标)，都是绝对路径字符串。

    文件工具是**精确**的（与执行时同一份路径数学）；bash 是**启发式**的（只扫字面量）。
    """
    from ..tools import workspace  # 函数内 import：避免 policy ↔ tools 的包级环

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
    """一次工具调用的归一化事实。**不含裁决**——裁决在 :mod:`avid.policy.engine`。"""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    normalized: str = ""
    targets: tuple[str, ...] = ()
    outside: tuple[str, ...] = ()
    credentials: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    damage: str | None = None
    network: bool = False
    operations: tuple[str, ...] = ()
    workspace_root: str | None = None

    @property
    def danger(self) -> str | None:
        """第一个危险类别名（给文案用）。"""
        return self.risks[0] if self.risks else None

    def ledger_key(self) -> tuple[str, str]:
        """能力账本的记账键：bash 按归一化命令原文，其它按目标路径，都没有则整份参数。

        键的**类型**就是能力类型（``command`` / ``path``）——这正是原则⑦要的东西：
        升级是"授予这条命令/这个路径"，不是"关掉沙箱"。
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


def brokerize(
    name: str, arguments: Any, *, root: str | None = None, mode: str | None = None
) -> Action:
    """把一次工具调用变成 :class:`Action`。``mode`` 只用于兜底判断（full 时区外不设限）。"""
    args = arguments if isinstance(arguments, dict) else {}
    command = args.get("command") if isinstance(args.get("command"), str) else None

    targets, outside, credentials = _scan_paths(name, args, root)
    # full（approval=none）下"区外"这个事实仍然要被记录与审计，只是不再拦——
    # 所以这里不做任何过滤，是否设限由 engine 按三轴裁决。
    risks = list(danger_categories(name, args))
    if outside and "越界" not in risks:
        risks.append("越界")
    normalized = normalize_command(command) if command else ""

    return Action(
        tool=name,
        arguments=args,
        normalized=normalized,
        targets=targets,
        outside=outside,
        credentials=credentials,
        risks=tuple(risks),
        damage=hard_deny(name, args),
        network=reaches_network(name, args),
        operations=_operations_for(name, command),
        workspace_root=root,
    )


def is_unrestricted(approval: str) -> bool:
    """full 档：连"区外"都不设限。单独成函数，好让测试直接盯这条语义。"""
    return approval == APPROVAL_NONE


__all__ = [
    "APPROVAL_RULES",
    "COST_RULES",
    "DANGER_PATTERNS",
    "DENY_PATTERNS",
    "NETWORK_HINTS",
    "OPERATION_READ",
    "OPERATION_WRITE",
    "PATH_TOOLS",
    "SENSITIVE_ABSOLUTE",
    "SENSITIVE_COMPONENTS",
    "SENSITIVE_SUFFIX",
    "WRITE_TOOLS",
    "Action",
    "brokerize",
    "command_key",
    "danger_categories",
    "danger_reason",
    "hard_deny",
    "is_unrestricted",
    "normalize_command",
    "reaches_network",
    "sensitive_reason",
]
