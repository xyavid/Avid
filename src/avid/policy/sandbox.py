"""Sandbox Manager：把"沙箱策略"变成一份可执行的 argv、环境与挂载。

原则②的落点：**安全边界在 LLM 之外**。模型可以要求任何东西，能生效的只有这里算出来
的 mount 与 network namespace——它是子进程的物理事实，不是提示词里的约定。

后端只有 bwrap 一个（实测在 WSL2 上可用）：

* FS：``--ro-bind / /`` 让整个系统只读，再 ``--bind <工作区> <工作区>`` 放开工作区；
* Secrets：对宿主凭据目录 ``--tmpfs``（空目录）掩蔽、对凭据文件 bind 一个空文件；
* Env：``--clearenv`` 只放白名单，凭据类变量名一律剥离；
* Network：``network=restricted`` → ``--unshare-net``，子进程零出网。

**为什么不做 Landlock 兜底**：Landlock ABI 3（本机实测）只能管文件系统，**网络规则要
ABI 4**。而三个预设里凡是有沙箱的组合都是 ``network=restricted``，所以 Landlock 单独
用只能管一半，还得额外起一个解释器来在子进程里 ``restrict_self``。它的探测结果如实
上报（:func:`landlock_abi`），但不做半吊子实现——"一半的沙箱"比"说清楚没有沙箱"更危险。

**不静默降级**（原则⑥的推论）：``policy=workspace`` 而后端不可用时，规格里
``available=False``，决策层据此把边界挪回人身上（manual 逐个问）或直接拒（auto）；
本模块只如实报告，不做任何"那就先跑吧"的决定。
"""

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

logger = logging.getLogger("avid.policy.sandbox")

BACKEND_BWRAP = "bwrap"
BACKEND_NONE = "none"

#: 允许指向另一个 bwrap 实现（诊断/打包场景）。它只换"用哪个二进制"，不换语义。
SANDBOX_BIN_ENV = "AVID_SANDBOX_BIN"

#: 掩蔽的宿主凭据目录（``--tmpfs``：目录还在，里面是空的）。
DEFAULT_MASK_DIRS: tuple[str, ...] = (
    # 代理自己的配置与审计：模型连"读过它"都不该可能（ADMIN 规则同时禁止工具的读写，
    # 这里再让 bash 连目录都看不见——两层各自独立，任何一层失效都还有另一层）。
    "~/.avid",
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gh",
    "~/.kube",
    "~/.config/gcloud",
)

#: 掩蔽的宿主凭据/配置文件（bind 一个空文件，连内容都看不见）。
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

#: 环境变量白名单：放行的是"跑命令需要的"，不是"顺手全带上"。
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
        # 语言/构建工具链的路径类变量：不含凭据。
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

#: 即使命中白名单也要剥离的名字（防"白名单前缀 + 凭据名"的组合漏网）。
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

#: 刻意**不**透传的东西：X11/Wayland 能操作宿主桌面（截图、模拟按键），
#: 各种 runtime dir 里是同用户的 socket。沙箱里它们都不该存在。
ENV_DENY_EXACT: frozenset[str] = frozenset(
    {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS"}
)

_PROBE_TIMEOUT = 10.0


def landlock_abi() -> int | None:
    """内核 Landlock ABI 版本（拿不到返回 ``None``）。**只用于上报**，见模块 docstring。"""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:  # pragma: no cover - 非 glibc 平台
        return None
    # landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION) 返回 ABI 版本
    result = libc.syscall(444, None, 0, 1)
    return int(result) if result > 0 else None


@dataclass(frozen=True)
class BackendProbe:
    """后端探测结果：``available`` 是**实测**跑通一次隔离，不是"命令存在"。"""

    backend: str = BACKEND_NONE
    binary: str | None = None
    available: bool = False
    network_isolation: bool = False
    reason: str | None = "未探测"
    landlock: int | None = None

    def summary(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "available": self.available,
            "network": self.network_isolation,
            "reason": self.reason,
            "landlock_abi": self.landlock,
        }


@lru_cache(maxsize=4)
def probe_backend(binary: str | None = None) -> BackendProbe:
    """探测 bwrap：先找二进制，再**真跑一次**只读 + 断网的沙箱。

    "命令存在"不等于"能用"（容器里 bwrap 常装上却因权限不够而失败），所以可用性必须
    用一次真实执行来判定，并且结果缓存——每次运行都探测会拖慢启动。
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
        done = subprocess.run(  # noqa: S603 - argv 是常量，只换二进制路径
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
    """一次运行的沙箱规格。**数据**：能不能用、要挂什么、argv 长什么样。"""

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
    #: 这次运行认定的"宿主家目录"：掩蔽清单与子进程的 ``HOME`` 都以它为准。
    #: 两者各自从环境里算会分家——掩蔽 A 家的凭据、却把 B 家设成 HOME。
    home: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ---------------------------------------------------------------- 语义

    @property
    def enforced(self) -> bool:
        """这次运行是否**真的**套了沙箱。"""
        return self.policy == SANDBOX_WORKSPACE and self.available

    @property
    def degraded(self) -> bool:
        """要求沙箱但后端不可用。

        决策层据此把边界挪回人/分类器；工具层不做任何决定。
        """
        return self.policy == SANDBOX_WORKSPACE and not self.available

    def summary(self) -> dict[str, object]:
        """进 ``run_started`` 事件与审计的形状：**可见**才谈得上可审计。"""
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
        """给人看的一行（CLI/界面）：full 永远看得见。"""
        if self.policy == SANDBOX_DISABLED:
            return "沙箱：已禁用（full）"
        if self.enforced:
            net = "无出网" if self.network == NETWORK_RESTRICTED else "网络不限"
            return f"沙箱：工作区（{self.backend}，{net}）"
        return f"沙箱：不可用（{self.reason or '未知原因'}）"

    # ---------------------------------------------------------------- 执行

    def apply_env(self, source: Mapping[str, str] | None = None) -> dict[str, str]:
        """按白名单裁一份子进程环境。不套沙箱时也照裁——凭据不该进任何子进程。"""
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
        """子进程该用的环境：**真正套了沙箱**时返回裁过的白名单，否则 ``None``（继承）。

        降级与 full 都返回 ``None``：边界不生效时，环境也不该被"半个沙箱"改动——
        半管不管比说清楚更难排查。那时兜底的是审批/分类器。
        """
        return self.apply_env(source) if self.enforced else None

    def argv_prefix(
        self,
        command_argv: list[str],
        *,
        grants: Iterable[tuple[str, str]] = (),
        root: str | None = None,
    ) -> list[str]:
        """把命令 argv 前面套上沙箱。``grants`` 是本次运行已获准的能力（路径 + ro/rw）。

        不套沙箱（``policy=disabled`` 或不 ``available``）时逐字返回原 argv——降级要发生
        在决策层，而不是这里悄悄不套。
        """
        if not self.enforced:
            return list(command_argv)
        if self.binary is None:  # pragma: no cover - enforced 蕴含 binary 非空
            return list(command_argv)

        workdir = root or self.root
        # 挂载顺序是**语义**的一部分（后挂的盖住先挂的）。顺序与理由：
        #   1 只读系统
        #   2 /dev /proc 与一个空 /tmp   ← 工作区常在 /tmp 下，先挂空 /tmp 才盖得住它
        #   3 能力授予                   ← 用户批准过的区外路径
        #   4 掩蔽宿主凭据               ← **必须晚于授予**：批准一个父目录（例如 $HOME）
        #                                  不能连带把它的 `.ssh` 掀开。掩蔽是宿主策略，
        #                                  授予是本次运行的能力，两者冲突时掩蔽赢。
        #   5 工作区（可写）
        argv: list[str] = [self.binary, "--ro-bind", "/", "/"]
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]

        # 能力授予：区外路径按用户/分类器批准的口径挂进来。
        for path, access in self._allowed_grants(grants):
            flag = "--bind" if access == "rw" else "--ro-bind"
            argv += [flag, path, path]

        for directory in self.mask_dirs:
            if workdir and _contains(directory, workdir):
                continue  # 工作区在被掩蔽的目录里：工作区优先，跳过这条掩蔽
            if Path(directory).is_dir():
                argv += ["--tmpfs", directory]

        empty = _empty_mask_file()
        for item in self.mask_files:
            if workdir and _contains(item, workdir):
                continue
            if Path(item).is_file() and empty is not None:
                argv += ["--ro-bind", str(empty), item]

        if workdir:
            argv += ["--bind", workdir, workdir]
        if self.network == NETWORK_RESTRICTED:
            argv += ["--unshare-net"]
        argv += [
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--new-session",
            "--die-with-parent",
            "--clearenv",
        ]
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
        """过滤掉掩蔽路径上的授予：掩蔽是宿主级策略，能力授予不能把它掀开。"""
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
    """``path`` 是否在 ``base`` 之内（含自身）。"""
    base_text = str(Path(base)).rstrip("/")
    text = str(Path(path)).rstrip("/")
    return text == base_text or text.startswith(base_text + "/")


@lru_cache(maxsize=1)
def _empty_mask_file() -> Path | None:
    """掩蔽文件用的空文件。放 ``~/.avid/run/``（我们自己拥有），不放共享 ``/tmp``。"""
    try:
        target = avid_home() / "run"
        target.mkdir(parents=True, exist_ok=True)
        path = target / "empty-mask"
        if not path.exists():
            path.touch(mode=0o600)
        return path
    except OSError as exc:  # pragma: no cover - 取决于磁盘权限
        logger.warning("建不出掩蔽用的空文件：%s", exc)
        return None


#: 调用方**没有提供**沙箱规格时用的那一份：按"要求沙箱但不可用"处理。
#:
#: 为什么不是"没有规格就当没有边界"：那样任何漏传 ``security`` 的调用点都会静默拿到
#: 无沙箱的执行权。失败方向必须是"更严"：受管工具退回 REVIEW，auto 直接拒。
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
    """由三轴 + 工作区根 + 后端探测结果算出一份规格。

    ``policy=disabled``（full）时不探测必要的可用性——它本来就不要沙箱，但后端信息仍然
    带出去，方便诊断"这台机器上 full 与非 full 的差别到底有没有"。
    """
    home_dir = Path(home) if home is not None else Path.home()
    found = probe if probe is not None else probe_backend()

    # 这几个字段在各分支里值类型不同（`root`/`binary` 允许 None），所以显式标成 Any：
    # 展开 `**common` 时 mypy 只能看到"值的联合"，会误报每一个字段。
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
    """算最终的掩蔽清单。工作区落在某个掩蔽路径里时跳过该条（并记一条 note）。

    **掩蔽的是解析后的真实路径**：``~/.aws`` 在 WSL 里常是符号链接
    （``-> /mnt/c/Users/...``），而 bwrap 不能在符号链接上挂 tmpfs（实测报
    "Can't mount tmpfs on …: No such file or directory"）。掩蔽真实路径反而更严：
    不管从哪条路径过去，看到的都是空的。
    """
    dirs: list[str] = []
    files: list[str] = []
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
    """给 ``GET /api/meta`` 的能力上报。"""
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
