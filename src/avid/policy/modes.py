"""三个用户模式 = 三轴预设；三轴在**实现上互相独立**。

模式是对外的一个词，内核里它是三个正交参数的组合：

=================  =================  =================  ==================
模式               approval           sandbox            network
=================  =================  =================  ==================
``manual``（默认） ``user``           ``workspace``      ``restricted``
``auto``           ``classifier``     ``workspace``      ``restricted``
``full``           ``none``           ``disabled``       ``open``
=================  =================  =================  ==================

三轴各自回答一个不同的问题，谁也不推导谁：

* ``approval`` 决定**多少人工监督**——谁回答"这次动作要边界外的能力"；
* ``sandbox`` 决定**即使判断错了、物理上还能造成多大伤害**；
* ``network`` 是一级安全边界，独立于文件系统（原则⑤）。

因此代码里**不允许**出现"``approval=classifier`` ⇒ 关沙箱"这类推导：``auto`` 与
``manual`` 的沙箱完全相同，差别只在"谁来回答 REVIEW"。判据是
``tests/test_modes.py::test_axes_are_orthogonal``（枚举所有预设，断言 approval 变化
不改变 sandbox/network，反之亦然）。

**``full`` 不是默认，也永远不许变成默认。** 它是"关掉最后一道边界"的显式授权，
所以有三重锁（:func:`full_grant_error`）：

1. CLI 必须同时给 ``--permission full`` 与 ``--allow-full-access``；
2. Web 请求体必须带 ``full_access_ack=true``，否则 422；
3. 工作区默认权限**不接受** ``full``（``source="workspace_default"`` 一律报错）——
   持久化一个"下次也关沙箱"的默认值，等于把显式授权变成静默授权。

**旧模式名迁移**（:data:`LEGACY_MODES`）：注册表里存过 ``strict`` / ``workspace`` /
``system``，读回来时按"收紧或持平"的方向映射，**绝不迁到 ``full``**。

* ``strict → manual``：旧 strict 的"每个受管动作都问"在新模型里由"沙箱内免问 + 沙箱
  保证"表达；越界与危险仍然问人。
* ``workspace → manual``（**不是 auto**）：旧 workspace 的语义是"危险命令问人"，
  而 ``auto`` 是"无人在环"。把"问人"迁成"无人"就是静默减少人工监督，方向错了。
* ``system → auto``：旧 system "默认免问、仅危险命令问"。``auto`` 把那一问交给
  分类器（判不准即拒），是收紧；``full`` 才是等价放宽，但它必须显式授权。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- 三轴取值

APPROVAL_USER = "user"
APPROVAL_CLASSIFIER = "classifier"
APPROVAL_NONE = "none"
APPROVALS: tuple[str, ...] = (APPROVAL_USER, APPROVAL_CLASSIFIER, APPROVAL_NONE)

SANDBOX_WORKSPACE = "workspace"
SANDBOX_DISABLED = "disabled"
SANDBOXES: tuple[str, ...] = (SANDBOX_WORKSPACE, SANDBOX_DISABLED)

NETWORK_RESTRICTED = "restricted"
NETWORK_OPEN = "open"
NETWORKS: tuple[str, ...] = (NETWORK_RESTRICTED, NETWORK_OPEN)


class PermissionModeError(ValueError):
    """未知模式名。显式报错，不静默回落到默认值。"""


class FullAccessError(PermissionModeError):
    """``full`` 缺少显式授权。它是拒绝启动的理由，不是回落到别的模式的理由。"""


@dataclass(frozen=True)
class Mode:
    """一个预设三元组。字段只有三轴 + 展示文案。"""

    name: str
    approval: str
    sandbox: str
    network: str
    label: str
    summary: str

    def axes(self) -> tuple[str, str, str]:
        return (self.approval, self.sandbox, self.network)


MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODE_FULL = "full"

MODES: tuple[str, ...] = (MODE_MANUAL, MODE_AUTO, MODE_FULL)
DEFAULT_MODE = MODE_MANUAL
FULL_MODE = MODE_FULL

MODE_TABLE: dict[str, Mode] = {
    MODE_MANUAL: Mode(
        name=MODE_MANUAL,
        approval=APPROVAL_USER,
        sandbox=SANDBOX_WORKSPACE,
        network=NETWORK_RESTRICTED,
        label="手动",
        summary="沙箱内免问；危险命令与越界一律问人",
    ),
    MODE_AUTO: Mode(
        name=MODE_AUTO,
        approval=APPROVAL_CLASSIFIER,
        sandbox=SANDBOX_WORKSPACE,
        network=NETWORK_RESTRICTED,
        label="自动",
        summary="沙箱内免问；越界与危险由分类器裁决，判不准即拒（不问人）",
    ),
    MODE_FULL: Mode(
        name=MODE_FULL,
        approval=APPROVAL_NONE,
        sandbox=SANDBOX_DISABLED,
        network=NETWORK_OPEN,
        label="完全（显式授权）",
        summary="不问、不套沙箱、不限制网络；必须显式授权，且不能作为默认值",
    ),
}

#: 展示文案（既有调用方按 ``MODE_LABELS[mode]`` 取一词标签）。
MODE_LABELS: dict[str, str] = {name: mode.label for name, mode in MODE_TABLE.items()}

#: 旧注册表值 → 新模式。方向一律"收紧或持平"（见模块 docstring）。
LEGACY_MODES: dict[str, str] = {
    "strict": MODE_MANUAL,
    "workspace": MODE_MANUAL,
    "system": MODE_AUTO,
}


def validate_mode(value: object) -> str:
    """新模式名白名单。旧名一律先走 :func:`migrate_mode`。"""
    if value not in MODE_TABLE:
        raise PermissionModeError(
            f"未知权限模式 {value!r}；可用：{'、'.join(MODES)}"
        )
    return str(value)


def migrate_mode(value: object) -> tuple[str, str | None]:
    """把注册表里读到的值迁成新模式。

    返回 ``(模式, 迁移说明或 None)``。说明非空时调用方要**打出来**：静默改掉用户
    存过的安全设置是不允许的。
    """
    if value in MODE_TABLE:
        return str(value), None
    if value in LEGACY_MODES:
        mapped = LEGACY_MODES[str(value)]
        return mapped, f"旧模式名 {value!r} 已迁移为 {mapped!r}"
    raise PermissionModeError(
        f"未知权限模式 {value!r}；可用：{'、'.join(MODES)}"
        f"（旧名 {'、'.join(LEGACY_MODES)} 会被迁移）"
    )


def mode_spec(name: str) -> Mode:
    return MODE_TABLE[validate_mode(name)]


def resolve_axes(name: object) -> tuple[str, str, str]:
    """模式 → ``(approval, sandbox, network)``。这是唯一的解析点。"""
    return mode_spec(validate_mode(name)).axes()


# ------------------------------------------------------------ full 三重锁

ADMIN_WRITABLE_SOURCES = ("cli", "web", "api")

FULL_ACK_HINT = (
    "full 会关掉沙箱与网络边界，因此必须显式授权："
    "CLI 加 --allow-full-access，Web 在请求体里带 full_access_ack=true"
)


def full_grant_error(
    mode: object, *, acknowledged: bool = False, source: str = "cli"
) -> str | None:
    """``full`` 是否缺显式授权；缺则返回**可读的拒绝理由**，否则 ``None``。

    ``source`` 是这次授权请求来自哪里：

    * ``"cli"`` / ``"web"``：允许，但必须 ``acknowledged=True``；
    * ``"workspace_default"``：**一律拒绝**——full ≠ default 是产品规则。
    """
    if mode != FULL_MODE:
        return None
    if source == "workspace_default":
        return (
            "full 不能作为工作区默认权限（full ≠ default）："
            "它必须由人在某一次运行里显式授权"
        )
    if not acknowledged:
        return f"permission=full 缺少显式授权。{FULL_ACK_HINT}"
    return None


__all__ = [
    "ADMIN_WRITABLE_SOURCES",
    "APPROVAL_CLASSIFIER",
    "APPROVAL_NONE",
    "APPROVAL_USER",
    "APPROVALS",
    "DEFAULT_MODE",
    "FULL_ACK_HINT",
    "FULL_MODE",
    "FullAccessError",
    "LEGACY_MODES",
    "MODE_AUTO",
    "MODE_FULL",
    "MODE_LABELS",
    "MODE_MANUAL",
    "MODE_TABLE",
    "MODES",
    "Mode",
    "NETWORK_OPEN",
    "NETWORK_RESTRICTED",
    "NETWORKS",
    "PermissionModeError",
    "SANDBOX_DISABLED",
    "SANDBOX_WORKSPACE",
    "SANDBOXES",
    "full_grant_error",
    "migrate_mode",
    "mode_spec",
    "resolve_axes",
    "validate_mode",
]
