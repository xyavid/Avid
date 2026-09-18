"""执行前的权限裁决：四层按模式判断。

单一入口是 :func:`gate`，顺序固定，先严后宽：

1. **硬拒绝** —— 命中黑名单即拒绝，用户无法覆盖（``--yes`` 也不行）
2. **危险命令** —— 与模式无关，一律问，并在理由里写明类别
3. **越界** —— 目标在工作区之外：``system`` 放行，``strict``/``workspace`` 问一次
4. **常规规则** —— ``strict`` 问，``workspace``/``system`` 放行

模式名就是信任边界（``strict`` / ``workspace`` / ``system``），"哪些动作打问号"满足
``strict ⊇ workspace ⊇ system``。完整规格见 ``docs/design/workspace-permission.md``。

**同意一次即生效**由 :class:`ApprovalLedger` 记账：危险命令按规范化后的命令原文记，
越界按绝对路径记，同一次运行内不再重复问。账本只在内存里、只活一次运行；子 agent 与父
agent 共用一本（带锁，因为 subagent 在别的线程并行跑）。

**这不是沙箱，也不替代沙箱。** 黑名单挡的是"手滑一次就不可恢复"的命令；危险清单挡的是
"会在工作区之外产生副作用"的命令；变量展开、base64、引号拼接、脚本文件都能绕过它们。
这里的目标只是把危险与越界变成一次确认，不是声称安全。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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

# 危险命令：会在工作区之外产生副作用、不可逆地丢数据、或取得更高权限。
# 与模式无关——三种模式下都要问一次；同意后按命令原文记账。只对 bash 判定。
DANGER_PATTERNS: tuple[tuple[str, str], ...] = (
    (_CMD_START + r"(?:sudo|su|doas|pkexec)\b", "提权"),
    (
        # 短选项组合（-r / -f / -rf / -Rf）与**长选项**（--recursive / --force / --dir）
        # 都要认：只匹配 `-[a-zA-Z]*[rRf]` 时 `rm --recursive x` 会整个漏过危险层，
        # 在 workspace/system 模式下**完全不问**就放行。
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

# 敏感路径（提权凭据、云密钥、私钥、影子口令）：读取或写入都算危险。
#
# 判定用"展开 + 路径分量"而不是正则字面量：`~/.ssh/config`、`$HOME/.ssh/config`、
# `/home/u/.ssh/config` 是同一个目标，只看字面量会漏掉后两种——而 system 模式对
# 区外是直接放行的，漏网就是静默放行。
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

# 工具名 → 需要审批的原因。不在这里的工具一律直接放行（区内只读工具）。
APPROVAL_RULES: dict[str, str] = {
    "bash": "执行 shell 命令",
    "write_file": "写入文件（已有内容会被覆盖）",
    "edit_file": "修改文件内容",
    "subagent": "并行派发 subagent（会额外消耗多次模型调用）",
}

# 权限模式：名字即信任边界。
MODE_STRICT = "strict"
MODE_WORKSPACE = "workspace"
MODE_SYSTEM = "system"
MODES: tuple[str, ...] = (MODE_STRICT, MODE_WORKSPACE, MODE_SYSTEM)
DEFAULT_MODE = MODE_STRICT
MODE_LABELS: dict[str, str] = {
    MODE_STRICT: "严格（每个受管动作都要问）",
    MODE_WORKSPACE: "工作区（区内常规操作免问，越界需同意）",
    MODE_SYSTEM: "系统级（默认免问，仅危险命令问）",
}

# 回传给模型的文案。三类拒绝给三条不同的下一步指引（"永远不许"与"这次不行"
# 对模型意味着完全不同的事，混为一谈会让它反复重试）。
HARD_MESSAGE = (
    "Permission denied. 原因：硬拒绝（{reason}）。"
    "这条命令被永久禁止，不要重试、也不要改写绕过，请改用别的方式完成任务。"
)
DANGER_MESSAGE = (
    "Permission denied. 原因：危险命令未获批准（{reason}）。"
    "不要重复提交同一条命令；请改用非破坏性做法，或说明你需要它做什么。"
)
OUTSIDE_MESSAGE = (
    "Permission denied. 原因：目标在工作区之外且未获批准（{reason}）。"
    "不要重复尝试同一路径；请在工作区内完成，或说明为什么需要它。"
)
USER_MESSAGE = (
    "Permission denied. 原因：本次未获用户批准。"
    "不要重复提交同一条调用；请说明你需要它做什么，或改用其它工具。"
)

# 工具层没有授权时的兜底文本（gate 没批准、或工具被直接调用时生效）。
OUTSIDE_TOOLS_ERROR = "拒绝访问工作区外的路径："

AskUser = Callable[[str, dict[str, Any], str], bool]


class PermissionModeError(ValueError):
    """未知模式名。显式报错，不静默回落到默认值。"""


def validate_mode(value: object) -> str:
    if value not in MODES:
        raise PermissionModeError(
            f"未知权限模式 {value!r}；可用：{'、'.join(MODES)}"
        )
    return str(value)


# 多个 subagent 并行时可能同时来要审批，而终端只有一个。
#
# 免审批开关本身由 RunState 显式传递，不再用 ContextVar：子 agent 在别的线程跑，
# contextvars 不跨线程继承，隐式状态在那里会静默失效；显式传参则传不过去就报错。
_ASK_LOCK = threading.Lock()


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


def hard_deny(name: str, arguments: Any) -> str | None:
    """第 1 层：只对 bash 的 command 做黑名单匹配，命中返回拒绝原因。"""
    if name != "bash" or not isinstance(arguments, dict):
        return None

    command = arguments.get("command")
    if not isinstance(command, str):
        return None

    for pattern, reason in DENY_PATTERNS:
        if re.search(pattern, command, re.MULTILINE):
            return reason
    return None


def danger_reason(name: str, arguments: Any) -> str | None:
    """第 2 层：命中危险清单返回类别名；``None`` 表示不是危险命令。

    只对 bash 判定。敏感路径额外覆盖文件类工具的 path 参数（按解析后的绝对路径判，
    见 :func:`sensitive_reason`）。
    """
    if not isinstance(arguments, dict):
        return None

    if name != "bash":
        path = arguments.get("path")
        if isinstance(path, str) and sensitive_reason(path):
            return "敏感路径"
        return None

    command = arguments.get("command")
    if not isinstance(command, str):
        return None

    if _sensitive_in_command(command):
        return "敏感路径"
    for pattern, category in DANGER_PATTERNS:
        if re.search(pattern, command, re.MULTILINE):
            return category
    return None


def command_key(command: str) -> str:
    """危险命令与越界命令的记账键：规范化空白后逐字比较。"""
    return " ".join(command.split())


def _operation_key(arguments: dict[str, Any]) -> str:
    """危险操作的记账键：bash 按命令原文，其它工具按目标路径（没有则整份参数）。"""
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        return command_key(command)
    path = arguments.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)


class ApprovalLedger:
    """一次运行内的"已同意"账本。

    带锁是因为 subagent 在并行线程里跑，且与父 agent 共用同一本账。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: set[tuple[str, str]] = set()

    def remember(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._keys.add(key)

    def knows(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return key in self._keys

    def outside_allowed(self, path: object) -> bool:
        """文件工具据此放行越界路径。只读账本，不做任何决定。"""
        return self.knows(("outside", str(path)))

    def __len__(self) -> int:  # 便于测试与诊断
        with self._lock:
            return len(self._keys)


@dataclass(frozen=True)
class Decision:
    """一次裁决的完整结果：给人看的理由、给模型看的文案、以及记账键。"""

    allowed: bool
    kind: str = ""
    reason: str = ""
    message: str = ""
    key: tuple[str, str] | None = None

    def __bool__(self) -> bool:
        return self.allowed


def match_rule(name: str, arguments: dict[str, Any]) -> str | None:
    """第 4 层：命中则返回需要审批的原因；返回 None 表示直接放行。

    arguments 目前不参与判断——内容级规则在 gate 里按模式与越界事实判断。
    """
    return APPROVAL_RULES.get(name)


def ask_user(name: str, arguments: dict[str, Any], reason: str) -> bool:
    """交互式确认。读不到输入一律拒绝。

    提示写 stderr，避免污染 stdout 上给用户看的最终答复。理由由 gate 组装，
    分类前缀（"危险命令…" / "越界操作…"）与目标路径都在里面，所以这里不再
    需要额外参数——审批回调的签名因此保持三参数不变（Web 的审批表同形）。
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


def gate(
    name: str,
    arguments: dict[str, Any],
    *,
    mode: str = DEFAULT_MODE,
    ask: AskUser | None = None,
    ledger: ApprovalLedger | None = None,
    danger: str | None = None,
    outside: str | None = None,
) -> Decision:
    """四层依次判断，返回 :class:`Decision`。允许时 ``bool(decision)`` 为真。

    ``danger`` 与 ``outside`` 是**调用方算好的事实**（危险类别名 / 越界的绝对目标）：
    路径数学只有一份（``tools/workspace.py``），策略层不复制它。
    """
    mode = validate_mode(mode)

    reason = hard_deny(name, arguments)
    if reason:
        logger.warning("硬拒绝 %s：%s", name, reason)
        print(f"\n⛔ 已拒绝：{reason}", file=sys.stderr)
        return Decision(False, "hard", reason, HARD_MESSAGE.format(reason=reason))

    key: tuple[str, str] | None = None

    if danger:
        key = ("danger", _operation_key(arguments))
        reason = f"危险命令（{danger}）"
        kind = "danger"
    elif outside:
        key = ("outside", outside)
        reason = f"越界操作：目标 {outside} 在工作区之外"
        kind = "outside"
    else:
        rule = match_rule(name, arguments)
        if rule is None:
            return Decision(True)
        # workspace / system 预授权了工作区内的常规操作；区外已在上一层处理。
        if mode != MODE_STRICT:
            return Decision(True)
        reason = rule
        kind = "user"

    if kind == "outside" and mode == MODE_SYSTEM:
        return Decision(True, kind, reason)

    if key is not None and ledger is not None and ledger.knows(key):
        logger.info("%s 复用已同意（%s）", name, reason)
        return Decision(True, kind, reason, key=key)

    answerer = ask_user if ask is None else ask
    if answerer(name, arguments, reason):
        if key is not None and ledger is not None:
            ledger.remember(key)
        logger.info("审批通过 %s：%s", name, reason)
        return Decision(True, kind, reason, key=key)

    logger.warning("审批拒绝 %s：%s", name, reason)
    if kind == "danger":
        # 用类别名而不是 `reason`：否则回给模型的文案会变成
        # "危险命令未获批准（危险命令（提权））"。
        message = DANGER_MESSAGE.format(reason=danger or reason)
    elif kind == "outside":
        message = OUTSIDE_MESSAGE.format(reason=outside or reason)
    else:
        message = USER_MESSAGE
    return Decision(False, kind, reason, message, key=key)


def check_permission(
    name: str,
    arguments: dict[str, Any],
    *,
    ask: AskUser | None = None,
    mode: str = DEFAULT_MODE,
    ledger: ApprovalLedger | None = None,
    danger: str | None = None,
    outside: str | None = None,
) -> bool:
    """四层依次判断，返回 **bool**（``gate`` 的薄封装，便于既有调用方与测试）。"""
    decision = gate(
        name,
        arguments,
        mode=mode,
        ask=ask,
        ledger=ledger,
        danger=danger,
        outside=outside,
    )
    return decision.allowed


def _always_allow(name: str, arguments: dict[str, Any], reason: str) -> bool:
    return True


# ``--yes`` 用的公开回答者：对每次询问都答"是"（硬拒绝仍由 gate 拦住）。
always_allow = _always_allow


def auto_approve(
    name: str,
    arguments: dict[str, Any],
    *,
    mode: str = DEFAULT_MODE,
    ledger: ApprovalLedger | None = None,
    danger: str | None = None,
    outside: str | None = None,
) -> bool:
    """``--yes`` 用：对本次运行的所有审批请求代答"是"，硬拒绝仍然生效。

    它只改变"谁来回答"，不改变"哪些动作会打问号"（那由模式决定）。
    """
    return check_permission(
        name,
        arguments,
        ask=_always_allow,
        mode=mode,
        ledger=ledger,
        danger=danger,
        outside=outside,
    )
