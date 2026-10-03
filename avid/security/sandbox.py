"""Sandbox manager: turns a sandbox policy into the bwrap argv, mounts and environment a child gets."""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .modes import NETWORK_RESTRICTED, SANDBOX_DISABLED, SANDBOX_WORKSPACE
from .userdirs import avid_home

logger = logging.getLogger("avid.security.sandbox")

BACKEND_BWRAP = "bwrap"
BACKEND_NONE = "none"

# Names an alternate bwrap binary for diagnostics or packaging; it swaps the binary, not the semantics.
SANDBOX_BIN_ENV = "AVID_SANDBOX_BIN"

# Host credential directories masked with an empty tmpfs; ~/.avid hides the agent's own audit.
DEFAULT_MASK_DIRS: tuple[str, ...] = (
    "~/.avid",
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gh",
    "~/.kube",
    "~/.config/gcloud",
)

# Host credential and shell-startup files masked by binding a shared empty file over each.
DEFAULT_MASK_FILES: tuple[str, ...] = (
    "~/.netrc",
    "~/.git-credentials",
    "~/.docker/config.json",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.profile",
    "~/.zshrc",
    "~/.bash_history",
    "~/.zsh_history",
)

# Environment names passed through because running a command needs them, not for convenience.
ENV_ALLOW_EXACT: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LANGUAGE",
        "TERM",
        "TZ",
        "USER",
        "LOGNAME",
        "SHELL",
        "PWD",
        "TMPDIR",
        "EDITOR",
        "VISUAL",
        "PAGER",
        "COLUMNS",
        "LINES",
        "VARIANT",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
        "GOPATH",
        "GOROOT",
        "GOMODCACHE",
        "GOCACHE",
        "NVM_DIR",
        "NODE_PATH",
        "CARGO_HOME",
        "RUSTUP_HOME",
        "JAVA_HOME",
        "MAVEN_HOME",
        "GRADLE_USER_HOME",
    }
)
ENV_ALLOW_PREFIXES: tuple[str, ...] = ("LC_",)

# Stripped even when whitelisted, which closes the prefix-plus-secret-name combination.
ENV_SECRET_MARKERS: tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "APIKEY",
    "API_KEY",
    "CREDENTIAL",
    "PRIVATE",
    "_AUTH",
    "COOKIE",
    "SESSION",
    "DSN",
    "DATABASE_URL",
)

# Deliberately withheld: X11/Wayland reach the host desktop and runtime dirs hold same-user sockets.
ENV_DENY_EXACT: frozenset[str] = frozenset(
    {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS"}
)

# Seconds one availability probe may run before the backend counts as unusable.
_PROBE_TIMEOUT = 10.0


def landlock_abi() -> int | None:
    """Return the kernel Landlock ABI version, or None when the syscall is unavailable; reported only."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:  # pragma: no cover - non-glibc platform
        return None
    # syscall 444 is landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION).
    result = libc.syscall(444, None, 0, 1)
    return int(result) if result > 0 else None


@dataclass(frozen=True)
class BackendProbe:
    """Probe result for the sandbox backend; available means one isolation run really succeeded."""

    backend: str = BACKEND_NONE
    binary: str | None = None
    available: bool = False
    network_isolation: bool = False
    reason: str | None = "未探测"
    landlock: int | None = None

    def summary(self) -> dict[str, object]:
        """Return the probe fields carried into sandbox capability reporting."""
        return {
            "backend": self.backend,
            "available": self.available,
            "network": self.network_isolation,
            "reason": self.reason,
            "landlock_abi": self.landlock,
        }


@lru_cache(maxsize=4)
def probe_backend(binary: str | None = None) -> BackendProbe:
    """Find bwrap and prove it usable with one read-only network-isolated run; the result is cached.

    An installed binary is not a working one, so availability is decided by real execution.
    """
    abi = landlock_abi()
    resolved = binary or os.environ.get(SANDBOX_BIN_ENV) or shutil.which(BACKEND_BWRAP)
    if not resolved:
        return BackendProbe(
            backend=BACKEND_NONE,
            available=False,
            reason="找不到 bubblewrap（bwrap）",
            landlock=abi,
        )

    # The probe argv mirrors the real prefix: read-only system, /dev, no network, die with parent.
    probe_argv = [
        resolved,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--unshare-net",
        "--die-with-parent",
        "--",
        shutil.which("true") or "/bin/true",
    ]
    try:
        done = subprocess.run(  # noqa: S603 - argv is constant, only the binary path is substituted
            probe_argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return BackendProbe(
            backend=BACKEND_NONE,
            binary=resolved,
            available=False,
            reason=f"bwrap 探测失败：{exc}",
            landlock=abi,
        )
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        return BackendProbe(
            backend=BACKEND_NONE,
            binary=resolved,
            available=False,
            reason=f"bwrap 探测退出码 {done.returncode}：{detail[-1] if detail else '无输出'}",
            landlock=abi,
        )
    return BackendProbe(
        backend=BACKEND_BWRAP,
        binary=resolved,
        available=True,
        network_isolation=True,
        reason=None,
        landlock=abi,
    )


@dataclass(frozen=True)
class SandboxSpec:
    """Sandbox specification for one run: whether it applies, what to mount, and the argv shape."""

    policy: str = SANDBOX_WORKSPACE
    network: str = NETWORK_RESTRICTED
    root: str | None = None
    backend: str = BACKEND_NONE
    binary: str | None = None
    available: bool = False
    reason: str | None = None
    mask_dirs: tuple[str, ...] = ()
    mask_files: tuple[str, ...] = ()
    env_allow: tuple[str, ...] = ()
    # Host home for this run: both the mask list and the child HOME derive from it, or they diverge.
    home: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    # Semantics

    @property
    def enforced(self) -> bool:
        """True only when the workspace sandbox is both requested and actually available."""
        return self.policy == SANDBOX_WORKSPACE and self.available

    @property
    def degraded(self) -> bool:
        """True when a sandbox was requested but the backend is unavailable.

        The decision layer moves the boundary back to a human or a classifier; the tool layer decides
        nothing.
        """
        return self.policy == SANDBOX_WORKSPACE and not self.available

    def summary(self) -> dict[str, object]:
        """Return the shape reported into the run's start event and the audit record."""
        return {
            "policy": self.policy,
            "network": self.network,
            "backend": self.backend,
            "available": self.available,
            "enforced": self.enforced,
            "degraded": self.degraded,
            "reason": self.reason,
            "root": self.root,
            "notes": list(self.notes),
        }

    def one_line(self) -> str:
        """Return the human-readable status line shown by the CLI and the UI."""
        if self.policy == SANDBOX_DISABLED:
            return "沙箱：已禁用（full）"
        if self.enforced:
            net = "无出网" if self.network == NETWORK_RESTRICTED else "网络不限"
            return f"沙箱：工作区（{self.backend}，{net}）"
        return f"沙箱：不可用（{self.reason or '未知原因'}）"

    # Execution

    def apply_env(self, source: Mapping[str, str] | None = None) -> dict[str, str]:
        """Trim a child environment to the whitelist; credentials must not reach any child."""
        raw = dict(os.environ if source is None else source)
        kept: dict[str, str] = {}
        for name, value in raw.items():
            if name in ENV_DENY_EXACT:
                continue
            if any(mark in name.upper() for mark in ENV_SECRET_MARKERS):
                continue
            if name in ENV_ALLOW_EXACT or name.startswith(ENV_ALLOW_PREFIXES):
                kept[name] = value
        kept.setdefault("PATH", raw.get("PATH", "/usr/bin:/bin"))
        kept["HOME"] = self.home or kept.get("HOME") or str(Path.home())
        return kept

    def child_env(self, source: Mapping[str, str] | None = None) -> dict[str, str] | None:
        """Return the trimmed environment only when the sandbox is enforced, else None to inherit.

        Degraded runs inherit too, since a half-applied environment is harder to diagnose.
        """
        return self.apply_env(source) if self.enforced else None

    def argv_prefix(
        self,
        command_argv: list[str],
        *,
        grants: Iterable[tuple[str, str]] = (),
        root: str | None = None,
    ) -> list[str]:
        """Wrap a command in the sandbox prefix; grants are this run's approved out-of-workspace paths."""
        if not self.enforced:
            # A disabled or unavailable backend returns the argv untouched: degradation is not silent.
            return list(command_argv)
        if self.binary is None:  # pragma: no cover - enforced implies a non-empty binary
            return list(command_argv)

        workdir = root or self.root
        # Mount order is semantic: later mounts cover earlier ones, so grants come before masks.
        # An empty /tmp must be mounted before the workspace, which often lives under /tmp.
        argv: list[str] = [self.binary, "--ro-bind", "/", "/"]
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]

        # Capability grants: out-of-workspace paths mounted with the approved read/write access.
        for path, access in self._allowed_grants(grants):
            flag = "--bind" if access == "rw" else "--ro-bind"
            argv += [flag, path, path]

        for directory in self.mask_dirs:
            # A workspace inside the masked directory wins, so this mask is skipped.
            if workdir and _contains(directory, workdir):
                continue
            if Path(directory).is_dir():
                argv += ["--tmpfs", directory]

        empty = _empty_mask_file()
        for item in self.mask_files:
            # Same precedence as directories: the workspace is never masked out from under itself.
            if workdir and _contains(item, workdir):
                continue
            if Path(item).is_file() and empty is not None:
                argv += ["--ro-bind", str(empty), item]

        # The workspace is bound read-write last, so it covers every earlier mount on that path.
        if workdir:
            argv += ["--bind", workdir, workdir]
        if self.network == NETWORK_RESTRICTED:
            argv += ["--unshare-net"]
        # Unshare every namespace that could leak host state and start from an empty environment.
        argv += [
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--new-session",
            "--die-with-parent",
            "--clearenv",
        ]
        # --clearenv wiped everything, so the whitelist is re-injected explicitly.
        for name, value in self.apply_env().items():
            argv += ["--setenv", name, value]
        if workdir:
            argv += ["--chdir", workdir]
        argv.append("--")
        argv += list(command_argv)
        return argv

    def _allowed_grants(
        self, grants: Iterable[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Drop grants that land on a masked path: host policy outranks a run capability."""
        allowed: list[tuple[str, str]] = []
        for path, access in grants:
            if not path:
                continue
            resolved = Path(path)
            if not resolved.exists():
                continue
            text = str(resolved.resolve())
            masked = any(
                _contains(mask, text) or _contains(mask, path)
                for mask in (*self.mask_dirs, *self.mask_files)
            )
            if masked:
                logger.warning("拒绝在掩蔽路径上挂载能力：%s", path)
                continue
            allowed.append((text, access))
        return allowed


def _contains(base: str | Path, path: str | Path) -> bool:
    """Return True when path equals base or lies underneath it."""
    base_text = str(Path(base)).rstrip("/")
    text = str(Path(path)).rstrip("/")
    return text == base_text or text.startswith(base_text + "/")


@lru_cache(maxsize=1)
def _empty_mask_file() -> Path | None:
    """Return the empty file bound over masked files, created under ~/.avid/run and not /tmp."""
    try:
        target = avid_home() / "run"
        target.mkdir(parents=True, exist_ok=True)
        path = target / "empty-mask"
        if not path.exists():
            path.touch(mode=0o600)
        return path
    except OSError as exc:  # pragma: no cover - depends on disk permissions
        logger.warning("建不出掩蔽用的空文件：%s", exc)
        return None


# Fallback spec when the caller passes no sandbox spec: treated as required but unavailable.
# Failing closed keeps a forgotten spec from silently granting unsandboxed execution.
UNMANAGED = SandboxSpec(
    policy=SANDBOX_WORKSPACE,
    network=NETWORK_RESTRICTED,
    available=False,
    reason="运行未提供沙箱规格",
)


def build_spec(
    *,
    policy: str = SANDBOX_WORKSPACE,
    network: str = NETWORK_RESTRICTED,
    root: str | None = None,
    home: str | Path | None = None,
    probe: BackendProbe | None = None,
) -> SandboxSpec:
    """Compose a run's sandbox spec from the three axes, the workspace root and a backend probe."""
    home_dir = Path(home) if home is not None else Path.home()
    found = probe if probe is not None else probe_backend()

    # Typed Any because root and binary may be None; spreading **common otherwise misreports every field.
    common: dict[str, Any] = {
        "network": network,
        "root": root,
        "backend": found.backend,
        "binary": found.binary,
        "home": str(home_dir),
    }

    if policy == SANDBOX_DISABLED:
        return SandboxSpec(
            policy=SANDBOX_DISABLED,
            available=False,
            reason="沙箱被显式关闭（full）",
            **common,
        )

    notes: list[str] = []
    mask_dirs, mask_files = _masks(root, home_dir, notes)
    if root is None:
        return SandboxSpec(
            policy=SANDBOX_WORKSPACE,
            available=False,
            reason="没有运行级工作区根",
            mask_dirs=mask_dirs,
            mask_files=mask_files,
            notes=tuple(notes),
            **common,
        )
    if not found.available:
        return SandboxSpec(
            policy=SANDBOX_WORKSPACE,
            available=False,
            reason=found.reason or "后端不可用",
            mask_dirs=mask_dirs,
            mask_files=mask_files,
            notes=tuple(notes),
            **common,
        )
    if network == NETWORK_RESTRICTED and not found.network_isolation:
        return SandboxSpec(
            policy=SANDBOX_WORKSPACE,
            available=False,
            reason="后端不能强制网络边界（network=restricted 无法满足）",
            mask_dirs=mask_dirs,
            mask_files=mask_files,
            notes=tuple(notes),
            **common,
        )
    return SandboxSpec(
        policy=SANDBOX_WORKSPACE,
        available=True,
        mask_dirs=mask_dirs,
        mask_files=mask_files,
        env_allow=tuple(sorted(ENV_ALLOW_EXACT)),
        notes=tuple(notes),
        **common,
    )


def _masks(
    root: str | None, home: Path, notes: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve the final mask lists, skipping any mask that contains the workspace root."""
    dirs: list[str] = []
    files: list[str] = []
    # Masked paths are resolved: bwrap cannot mount a tmpfs over a symlink, and the target is stricter.
    for item in DEFAULT_MASK_DIRS:
        expanded = _expand_home(item, home)
        if root and _contains(expanded, root):
            notes.append(f"工作区位于 {item} 内，跳过该掩蔽")
            continue
        dirs.append(str(Path(expanded).resolve()))
    for item in DEFAULT_MASK_FILES:
        expanded = _expand_home(item, home)
        if root and _contains(expanded, root):
            notes.append(f"工作区位于 {item} 内，跳过该掩蔽")
            continue
        files.append(str(Path(expanded).resolve()))
    return tuple(dirs), tuple(files)


def _expand_home(pattern: str, home: Path) -> str:
    return str(home) + pattern[1:] if pattern.startswith("~") else pattern


def default_backend_summary() -> dict[str, object]:
    """Return the sandbox capability report consumed by the service's meta endpoint."""
    probe = probe_backend()
    summary = probe.summary()
    summary["spec"] = build_spec(policy=SANDBOX_WORKSPACE, probe=probe).summary()
    return summary


__all__ = [
    "BACKEND_BWRAP",
    "BACKEND_NONE",
    "BackendProbe",
    "DEFAULT_MASK_DIRS",
    "DEFAULT_MASK_FILES",
    "ENV_ALLOW_EXACT",
    "ENV_ALLOW_PREFIXES",
    "SANDBOX_BIN_ENV",
    "SandboxSpec",
    "UNMANAGED",
    "build_spec",
    "default_backend_summary",
    "landlock_abi",
    "probe_backend",
]
