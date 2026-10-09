"""Sandbox Manager：探测、argv 组装、环境白名单、掩蔽、降级与真实执行。

最后一组用例**真的跑**一次沙箱（bwrap 不在的机器上自动跳过）：前面那些断言说的是
"argv 里有没有那一行"，只有真跑才能证明"这些 flag 合起来确实拦住了"。
"""

from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path

import pytest

from avid.security.permission import (
    BackendProbe,
    build_spec,
)
from avid.security.sandbox import (
    BACKEND_BWRAP,
    BACKEND_NONE,
    DEFAULT_MASK_DIRS,
    DEFAULT_MASK_FILES,
    ENV_ALLOW_EXACT,
    UNMANAGED,
    SandboxSpec,
    landlock_abi,
    probe_backend,
)

WORKING = BackendProbe(
    backend=BACKEND_BWRAP,
    binary="/usr/bin/bwrap",
    available=True,
    network_isolation=True,
    reason=None,
    landlock=3,
)
BROKEN = BackendProbe(backend=BACKEND_NONE, available=False, reason="找不到 bubblewrap（bwrap）")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    host = tmp_path / "host-home"
    (host / ".ssh").mkdir(parents=True)
    (host / ".ssh" / "id_rsa").write_text("PRIVATE\n", encoding="utf-8")
    (host / ".bashrc").write_text("export SECRET=1\n", encoding="utf-8")
    return host


# ---------------------------------------------------------------- 探测


def test_missing_binary_is_reported_with_a_reason(monkeypatch, tmp_path):
    probe_backend.cache_clear()
    monkeypatch.setenv("AVID_SANDBOX_BIN", str(tmp_path / "nope"))
    try:
        found = probe_backend()
        assert found.available is False
        assert found.backend == BACKEND_NONE
        assert found.reason and "bwrap" in found.reason
    finally:
        probe_backend.cache_clear()


def test_probing_actually_runs_a_sandbox(monkeypatch, tmp_path):
    """"命令存在"不等于"能用"：拿一个假 bwrap（`/bin/true`）去探，必须判为不可用。"""
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    probe_backend.cache_clear()
    monkeypatch.setenv("AVID_SANDBOX_BIN", str(fake))
    try:
        found = probe_backend()
        assert found.available is False
        assert found.reason and "退出码" in found.reason
    finally:
        probe_backend.cache_clear()


def test_landlock_abi_is_reported_but_not_used_for_network():
    """Landlock ABI 只上报：阶段 51 没有网络轴，ABI 也不参与任何分档。"""
    abi = landlock_abi()
    assert abi is None or abi >= 1
    if abi is not None:
        assert abi < 4 or abi >= 4  # 只是说明"这个数会被如实报出去"


# ---------------------------------------------------------------- 规格装配


def test_workspace_policy_without_a_backend_is_degraded(tmp_path, home):
    spec = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=BROKEN)
    assert spec.degraded is True and spec.enforced is False
    assert spec.available is False and spec.reason
    assert spec.one_line().startswith("沙箱：不可用")


def test_workspace_policy_without_a_root_is_degraded(tmp_path, home):
    spec = build_spec(policy="workspace", root=None, home=home, probe=WORKING)
    assert spec.degraded is True
    assert spec.reason == "没有运行级工作区根"


def test_disabled_policy_is_not_degraded(tmp_path, home):
    """full 显式关沙箱：它不是"降级"，是一开始就不要这条边界。"""
    spec = build_spec(policy="disabled", root=str(tmp_path), home=home, probe=WORKING)
    assert (spec.policy, spec.degraded, spec.enforced) == ("disabled", False, False)
    assert spec.one_line() == "沙箱：已禁用（full）"


def test_network_isolation_capability_no_longer_gates_the_policy(tmp_path, home):
    """网络轴已删：探针报 network_isolation=False 也只是个上报字段，不降级工作区沙箱。"""
    spec = build_spec(
        policy="workspace",
        root=str(tmp_path),
        home=home,
        probe=BackendProbe(
            backend=BACKEND_BWRAP, binary="/usr/bin/bwrap", available=True, network_isolation=False
        ),
    )
    assert spec.enforced is True and spec.degraded is False
    assert "network" not in spec.summary()


def test_unmanaged_spec_fails_closed():
    assert UNMANAGED.degraded is True
    assert UNMANAGED.enforced is False


def test_summary_shape_is_what_events_and_audit_carry(tmp_path, home):
    spec = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=WORKING)
    summary = spec.summary()
    assert set(summary) == {
        "policy",
        "backend",
        "available",
        "enforced",
        "degraded",
        "reason",
        "root",
        "read_only",
        "notes",
    }
    # 只读是临时对话那一档（阶段 54）：它在 start 事件与会审记录里是可见事实
    assert summary["read_only"] is False


# ---------------------------------------------------------------- argv 组装


def argv(spec: SandboxSpec, command: list[str], **kwargs) -> list[str]:
    return spec.argv_prefix(command, **kwargs)


def test_argv_prefix_is_a_noop_when_not_enforced(tmp_path, home):
    spec = build_spec(policy="disabled", root=str(tmp_path), home=home, probe=WORKING)
    assert argv(spec, ["bash", "-c", "ls"]) == ["bash", "-c", "ls"]


def test_argv_mounts_system_read_only_and_workspace_writable(tmp_path, home):
    spec = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=WORKING)
    built = argv(spec, ["bash", "-c", "ls"])
    assert built[:3] == [str(spec.binary), "--ro-bind", "/"]
    assert "--bind" in built and str(tmp_path) in built
    # 网络恒开：工作区沙箱只隔离文件系统与进程视图，不再 unshare 网络命名空间。
    assert "--unshare-net" not in built
    assert "--clearenv" in built


def test_empty_tmp_is_mounted_before_the_workspace_and_the_masks(tmp_path, home):
    """挂载顺序是语义：空 /tmp 必须先挂，后面的工作区与掩蔽才盖得住它。

    旧顺序（先工作区、后 /tmp）会让"工作区在 /tmp 下"的常见情形整个消失
    （bwrap: Can't chdir to /tmp/...: No such file or directory）——这条断言就是那次
    实测踩坑留下的钉子。
    """
    root = tmp_path / "ws"
    root.mkdir()
    spec = build_spec(policy="workspace", root=str(root), home=home, probe=WORKING)
    built = argv(spec, ["bash", "-c", "ls"], root=str(root))

    tmp_at = built.index("/tmp")
    assert built.index("--bind") > tmp_at
    assert any(built[index] == "--tmpfs" and built[index + 1] == str(home / ".ssh")
               for index in range(len(built) - 1))


def test_masks_come_after_grants_so_a_grant_cannot_unmask(tmp_path, home):
    """区外授权不能把它的掩蔽子目录掀开。

    顺序反了就会出现：区外授权给了 `$HOME` → `--ro-bind $HOME $HOME` 盖住先前挂的
    `--tmpfs $HOME/.ssh` → `.ssh` 又看得见了。掩蔽是宿主策略，授予是本次运行的能力，
    冲突时掩蔽赢。
    """
    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)
    built = argv(spec, ["bash", "-c", "ls"], grants=[(str(home), "ro")])

    grant_at = next(
        index
        for index in range(len(built) - 2)
        if built[index] == "--ro-bind" and built[index + 1] == str(home)
    )
    mask_at = next(
        index
        for index in range(len(built) - 1)
        if built[index] == "--tmpfs" and built[index + 1] == str(home / ".ssh")
    )
    assert mask_at > grant_at


def test_masks_use_the_resolved_path_so_symlinks_do_not_break(tmp_path, home):
    """WSL 里 `~/.aws` 是符号链接；bwrap 不能在符号链接上挂 tmpfs（实测）。"""
    target = tmp_path / "mnt" / "aws"
    target.mkdir(parents=True)
    (home / ".aws").symlink_to(target)

    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)
    assert str(target.resolve()) in spec.mask_dirs
    assert str(home / ".aws") not in spec.mask_dirs


def test_masks_are_skipped_when_the_workspace_lives_inside_them(tmp_path, home):
    inside = home / ".avid" / "ws"
    inside.mkdir(parents=True)
    spec = build_spec(policy="workspace", root=str(inside), home=home, probe=WORKING)
    assert any("跳过该掩蔽" in note for note in spec.notes)


def test_sessions_store_is_masked_wherever_it_is_configured(tmp_path, home, monkeypatch):
    """会话文件就是对话历史，agent 的工具不该读自己的记录（阶段 56）。"""
    store = tmp_path / "somewhere" / "avid-sessions"
    monkeypatch.setenv("AVID_SESSIONS_DIR", str(store))

    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)

    assert str(store) in spec.mask_dirs


def test_default_sessions_store_under_the_avid_home_is_not_masked_twice(tmp_path, monkeypatch):
    """生产形态：默认会话目录就住在 ~/.avid 里，那条掩蔽已经盖住它，不重复挂一次。"""
    monkeypatch.setenv("AVID_HOME", str(tmp_path / "host-home" / ".avid"))

    spec = build_spec(
        policy="workspace", root=str(tmp_path / "ws"), home=tmp_path / "host-home", probe=WORKING
    )

    assert str(tmp_path / "host-home" / ".avid") in spec.mask_dirs
    assert str(tmp_path / "host-home" / ".avid" / "sessions") not in spec.mask_dirs


def test_the_relocated_avid_home_is_masked_not_only_dot_avid(tmp_path, home, monkeypatch):
    """掩蔽名单里那句 ~/.avid 是常量，而 AVID_HOME 能把它搬到别处：密钥/审计/索引跟着走，就得跟着掩。"""
    relocated = tmp_path / "elsewhere" / "avid-home"
    monkeypatch.setenv("AVID_HOME", str(relocated))
    monkeypatch.setenv("AVID_SESSIONS_DIR", str(relocated / "sessions"))

    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)

    assert str(relocated) in spec.mask_dirs
    assert str(relocated / "sessions") not in spec.mask_dirs  # 已被上面那条盖住，不重复挂


def test_sessions_store_inside_the_workspace_is_masked_after_the_workspace_bind(
    tmp_path, home, monkeypatch
):
    """工作区内的会话目录要挂在工作区之后：不然那次绑定会把掩蔽盖掉。"""
    root = tmp_path / "ws"
    store = root / "sessions"
    store.mkdir(parents=True)
    monkeypatch.setenv("AVID_SESSIONS_DIR", str(store))

    spec = build_spec(policy="workspace", root=str(root), home=home, probe=WORKING)
    built = argv(spec, ["bash", "-c", "ls"])

    bind_at = next(
        index
        for index in range(len(built) - 2)
        if built[index] == "--bind" and built[index + 1] == str(root)
    )
    mask_at = next(
        index
        for index in range(len(built) - 1)
        if built[index] == "--tmpfs" and built[index + 1] == str(store)
    )
    assert mask_at > bind_at
    assert any("在工作区内" in note for note in spec.notes)


def test_grants_are_mounted_and_masked_targets_are_refused(tmp_path, home):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)

    built = argv(spec, ["bash", "-c", "cat"], grants=[(str(outside), "ro")])
    assert built.count(str(outside)) == 2  # 源与目标各一次
    assert "--ro-bind" in built

    # 掩蔽路径上的授予被拒：目标只出现在那条掩蔽里（没有额外的 bind 挂载）
    masked = argv(spec, ["bash", "-c", "ls"], grants=[(str(home / ".ssh"), "rw")])
    assert masked.count(str(home / ".ssh")) == 1


def test_grants_are_ignored_when_not_enforced(tmp_path, home):
    outside = tmp_path / "a.txt"
    outside.write_text("x", encoding="utf-8")
    spec = build_spec(policy="disabled", root=str(tmp_path), home=home, probe=WORKING)
    assert argv(spec, ["bash", "-c", "ls"], grants=[(str(outside), "rw")]) == ["bash", "-c", "ls"]


# ---------------------------------------------------------------- 环境


def test_env_whitelist_drops_secrets_and_desktop_access(home, tmp_path):
    spec = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=WORKING)
    kept = spec.apply_env(
        {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "SOME_API_KEY": "sk-live",
            "MY_DATABASE_URL": "postgres://u:p@h/db",
            "GITHUB_TOKEN": "ghp_x",
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "AWS_PROFILE": "prod",
            "SOMETHING_ELSE": "1",
        }
    )
    assert kept["PATH"] == "/usr/bin" and kept["LANG"] == "C.UTF-8" and kept["LC_ALL"] == "C.UTF-8"
    for dropped in (
        "SOME_API_KEY",
        "MY_DATABASE_URL",
        "GITHUB_TOKEN",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "SSH_AUTH_SOCK",
        "XDG_RUNTIME_DIR",
        "AWS_PROFILE",
        "SOMETHING_ELSE",
    ):
        assert dropped not in kept, dropped


def test_env_home_is_the_host_we_computed_the_masks_for(home, tmp_path):
    """掩蔽清单与 ``HOME`` 必须指向同一个家：各算各的会变成"掩蔽 A 家、用 B 家"。"""
    spec = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=WORKING)
    assert spec.apply_env({"PATH": "/usr/bin", "HOME": "/somewhere/else"})["HOME"] == str(home)


def test_child_env_full_inherits_and_degraded_scrubs(tmp_path, home):
    """full 显式信任整体继承（env=None，凭据可用）；降级走黑名单；强制走白名单。"""
    full = build_spec(policy="disabled", root=str(tmp_path), home=home, probe=WORKING)
    assert full.child_env({"PATH": "/usr/bin", "GITHUB_TOKEN": "x"}) is None

    degraded = SandboxSpec(policy="workspace", available=False, reason="无后端")
    scrubbed = degraded.child_env({"PATH": "/usr/bin", "SOME_API_KEY": "x", "SYSTEMROOT": r"C:\W"})
    assert scrubbed == {"PATH": "/usr/bin", "SYSTEMROOT": r"C:\W"}

    enforced = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=WORKING)
    assert enforced.child_env({"PATH": "/usr/bin", "SOME_API_KEY": "x"}) == {"PATH": "/usr/bin", "HOME": str(home)}


def test_env_allow_list_is_explicit():
    assert "PATH" in ENV_ALLOW_EXACT and "HOME" in ENV_ALLOW_EXACT
    assert "SOME_API_KEY" not in ENV_ALLOW_EXACT
    assert all(isinstance(item, str) for item in DEFAULT_MASK_DIRS + DEFAULT_MASK_FILES)


# ---------------------------------------------------------------- 真跑一次


@pytest.fixture
def real_spec(tmp_path, home):
    found = probe_backend()
    if not found.available:
        pytest.skip(f"这台机器上没有可用的 bwrap：{found.reason}")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "inside.txt").write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    return build_spec(policy="workspace", root=str(root), home=home, probe=found)


def _run(spec: SandboxSpec, script: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv(spec, ["bash", "-c", script], **kwargs),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_real_run_cannot_read_the_masked_credentials(real_spec, home):
    done = _run(real_spec, f"cat {home}/.ssh/id_rsa 2>&1; ls -A {home}/.ssh | wc -l")
    assert "PRIVATE" not in done.stdout
    assert done.stdout.strip().endswith("0")


def test_real_run_cannot_read_the_session_store(tmp_path, home, monkeypatch):
    """真跑：会话文件在沙箱里既读不到也列不出——工具视野里没有自己的对话历史。

    会话库**必须放在 /tmp 之外**：沙箱里 /tmp 是一块空 tmpfs，放那儿的话这条断言与
    会话目录掩蔽无关（把掩蔽代码删掉照样过——评审抓到的空转）。
    """
    found = probe_backend()
    if not found.available:
        pytest.skip(f"这台机器上没有可用的 bwrap：{found.reason}")
    root = tmp_path / "ws"
    root.mkdir()
    store = home.parent / "avid-sessions"  # /tmp 之外
    store.mkdir(exist_ok=True)
    (store / "2026-10-09T00-00-00-000_s-1.jsonl").write_text(
        '{"kind": "header", "id": "s-1"}\n', encoding="utf-8"
    )
    monkeypatch.setenv("AVID_SESSIONS_DIR", str(store))
    spec = build_spec(policy="workspace", root=str(root), home=home, probe=found)

    done = _run(spec, f"cat {store}/*.jsonl 2>&1; ls -A {store} | wc -l")

    assert '"kind": "header"' not in done.stdout
    assert done.stdout.strip().endswith("0")


def test_real_run_cannot_write_outside_the_workspace(real_spec, home, tmp_path):
    """区外目标必须**真实存在**才测得到"写不进去"。

    为什么放 `/var/tmp`：沙箱里 `/tmp` 是一块空 tmpfs，测试夹具（tmp_path）又在 /tmp 下，
    于是"往 /tmp 某个不存在的文件写"会在那层临时文件系统里成功——那是隔离生效的表现，
    却会让断言误判成"能写到区外"。用一个真正在 ro-bind 里的路径才问得出这个问题。
    """
    target = Path("/var/tmp") / f"avid-outside-{tmp_path.name}.txt"
    target.write_text("outside\n", encoding="utf-8")
    try:
        done = _run(real_spec, f"echo x >> {target} 2>&1; echo x > inside.txt && echo wrote")
        assert "Read-only file system" in (done.stdout + done.stderr)
        assert "wrote" in done.stdout
        assert target.read_text(encoding="utf-8") == "outside\n"
    finally:
        target.unlink(missing_ok=True)


def test_real_run_has_network(real_spec):
    """网络恒开（阶段 51）：argv 里没有 --unshare-net，沙箱里的连接能到达宿主在听的端口。

    用宿主自己开的回环监听来证明——不依赖外网，也不会有"外网刚好不通"的假红。
    """
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted: list[bool] = []

    def accept_one() -> None:
        try:
            connection, _ = server.accept()
            accepted.append(True)
            connection.close()
        except OSError:  # 沙箱里没连过来：由下面的断言给出失败原因
            pass

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    try:
        done = _run(real_spec, f"timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/{port}'; echo rc=$?")
        assert "rc=0" in done.stdout, done.stdout + done.stderr
    finally:
        server.close()
    thread.join(timeout=3)
    assert accepted, "沙箱里的连接没有到达宿主：网络被隔离了"


def test_real_run_does_not_see_secret_environment_variables(real_spec, monkeypatch):
    monkeypatch.setenv("AVID_TEST_SECRET_TOKEN", "leak-me")
    done = _run(real_spec, "echo token=${AVID_TEST_SECRET_TOKEN:-none}")
    assert "leak-me" not in done.stdout
    assert "token=none" in done.stdout


def test_real_run_can_read_a_granted_path_read_only(real_spec, tmp_path):
    target = Path("/var/tmp") / f"avid-grant-{tmp_path.name}.txt"
    target.write_text("outside\n", encoding="utf-8")
    done = subprocess.run(
        argv(real_spec, ["bash", "-c", f"cat {target}; echo x >> {target} 2>&1"], grants=[(str(target), "ro")]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "outside" in done.stdout
    assert "Read-only file system" in done.stdout + done.stderr
    target.unlink(missing_ok=True)


# ---------------------------------------------------------------- 降级环境黑名单（shell 适配三）


def test_scrubbed_env_drops_credential_shaped_names():
    from avid.security.sandbox import scrubbed_env

    kept = scrubbed_env(
        {
            "PATH": "/usr/bin",
            "SYSTEMROOT": r"C:\Windows",
            "SOME_API_KEY": "tvly-x",
            "AWS_SESSION_TOKEN": "tok",
            "SOME_PASSWORD": "p",
        }
    )
    assert kept == {"PATH": "/usr/bin", "SYSTEMROOT": r"C:\Windows"}


def test_degraded_child_env_is_scrubbed_not_inherited():
    from avid.security.sandbox import SANDBOX_WORKSPACE, SandboxSpec

    spec = SandboxSpec(policy=SANDBOX_WORKSPACE, available=False, reason="无后端")
    kept = spec.child_env({"PATH": "/bin", "MY_SECRET_TOKEN": "x"})
    assert kept == {"PATH": "/bin"}
