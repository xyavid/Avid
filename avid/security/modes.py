"""Three user modes as presets over three independent axes: approval, sandbox and network."""

from __future__ import annotations

from dataclasses import dataclass

# Approval axis: who answers a request for a capability the sandbox does not guarantee.
APPROVAL_USER = "user"
APPROVAL_CLASSIFIER = "classifier"
APPROVAL_NONE = "none"
APPROVALS: tuple[str, ...] = (APPROVAL_USER, APPROVAL_CLASSIFIER, APPROVAL_NONE)

# Sandbox axis: what is still contained physically when a decision was wrong.
SANDBOX_WORKSPACE = "workspace"
SANDBOX_DISABLED = "disabled"
SANDBOXES: tuple[str, ...] = (SANDBOX_WORKSPACE, SANDBOX_DISABLED)

# Network axis: a first-class boundary that is independent of the filesystem.
NETWORK_RESTRICTED = "restricted"
NETWORK_OPEN = "open"
NETWORKS: tuple[str, ...] = (NETWORK_RESTRICTED, NETWORK_OPEN)


class PermissionModeError(ValueError):
    """An unknown mode name; it is reported instead of silently falling back to the default."""


class FullAccessError(PermissionModeError):
    """``full`` was requested without an explicit grant, so startup is refused, not relaxed."""


@dataclass(frozen=True)
class Mode:
    """One preset: the three axis values plus its display label and summary."""

    name: str
    approval: str
    sandbox: str
    network: str
    label: str
    summary: str

    def axes(self) -> tuple[str, str, str]:
        return (self.approval, self.sandbox, self.network)


# Mode names; MODE_TABLE below is the single definition of each preset.
MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODE_FULL = "full"

# The default is manual, so full stays reachable only through an explicit grant.
MODES: tuple[str, ...] = (MODE_MANUAL, MODE_AUTO, MODE_FULL)
DEFAULT_MODE = MODE_MANUAL
FULL_MODE = MODE_FULL

# The axes never derive from one another: auto and manual share the same sandbox and network.
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
        summary="只读与工作区内写自动放行；网络、越界、危险与判不准的命令征询用户（无询问通道时拒绝）",
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

# Display wording, looked up as ``MODE_LABELS[mode]`` by existing callers.
MODE_LABELS: dict[str, str] = {name: mode.label for name, mode in MODE_TABLE.items()}

# Legacy registry values migrate toward a tighter or equal mode and never to full.
LEGACY_MODES: dict[str, str] = {
    # The old strict semantics survive as the sandbox plus review of outside and dangerous calls.
    "strict": MODE_MANUAL,
    # The old workspace mode asked a human about danger, while auto has nobody in the loop.
    "workspace": MODE_MANUAL,
    # The old system mode asked only about danger; the classifier is the tighter equivalent.
    "system": MODE_AUTO,
}


def validate_mode(value: object) -> str:
    """Validates a current mode name; legacy names must go through :func:`migrate_mode` first."""
    if value not in MODE_TABLE:
        raise PermissionModeError(
            f"未知权限模式 {value!r}；可用：{'、'.join(MODES)}"
        )
    return str(value)


def migrate_mode(value: object) -> tuple[str, str | None]:
    """Maps a stored registry value to a current mode, returning the mode and an optional note."""
    if value in MODE_TABLE:
        return str(value), None
    if value in LEGACY_MODES:
        mapped = LEGACY_MODES[str(value)]
        # The note must be printed: rewriting a stored security setting silently is not allowed.
        return mapped, f"旧模式名 {value!r} 已迁移为 {mapped!r}"
    raise PermissionModeError(
        f"未知权限模式 {value!r}；可用：{'、'.join(MODES)}"
        f"（旧名 {'、'.join(LEGACY_MODES)} 会被迁移）"
    )


def mode_spec(name: str) -> Mode:
    """Returns the preset for a validated mode name."""
    return MODE_TABLE[validate_mode(name)]


def resolve_axes(name: object) -> tuple[str, str, str]:
    """Maps a mode to ``(approval, sandbox, network)``; the only place that resolution happens."""
    return mode_spec(validate_mode(name)).axes()


# Sources that may request full access; mirrors the allowed sources pinned by tests.
ADMIN_WRITABLE_SOURCES = ("cli", "web", "api")

# Appended to the refusal so the caller learns how to acknowledge full access.
FULL_ACK_HINT = (
    "full 会关掉沙箱与网络边界，因此必须显式授权："
    "CLI 加 --allow-full-access，Web 在请求体里带 full_access_ack=true"
)


def full_grant_error(
    mode: object, *, acknowledged: bool = False, source: str = "cli"
) -> str | None:
    """Returns a readable refusal when ``full`` lacks an explicit grant, else ``None``."""
    if mode != FULL_MODE:
        return None
    if source == "workspace_default":
        # A workspace default is always refused, so full cannot become a silent authorization.
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
