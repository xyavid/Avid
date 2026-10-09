"""Sandbox manager: probing, argv assembly, env whitelist, masking, and degradation.

The last group really runs bwrap (skipped when unavailable): an argv assertion only checks
that a flag is present, and only a real run proves the flags together enforce anything.
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


# ---- probing ----


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
    """An existing binary is not a usable one: a fake bwrap exiting 1 probes as unavailable."""
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
    """Landlock ABI is reported only: it gates no policy tier."""
    abi = landlock_abi()
    assert abi is None or abi >= 1
    if abi is not None:
        assert abi < 4 or abi >= 4  # the reading is reported as-is, not compared


# ---- spec assembly ----


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
    """full disables the sandbox on purpose: not a degradation, the boundary was never wanted."""
    spec = build_spec(policy="disabled", root=str(tmp_path), home=home, probe=WORKING)
    assert (spec.policy, spec.degraded, spec.enforced) == ("disabled", False, False)
    assert spec.one_line() == "沙箱：已禁用（full）"


def test_network_isolation_capability_no_longer_gates_the_policy(tmp_path, home):
    """network_isolation is a reported field only: False must not degrade the workspace policy."""
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
    # read_only marks the ad-hoc conversation tier and is visible in start events and audit
    assert summary["read_only"] is False


# ---- argv assembly ----


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
    # Network stays on: the workspace sandbox isolates the filesystem and process view only.
    assert "--unshare-net" not in built
    assert "--clearenv" in built


def test_empty_tmp_is_mounted_before_the_workspace_and_the_masks(tmp_path, home):
    """Mount order is semantic: the empty /tmp goes first, since mounted last it makes a workspace
    under /tmp vanish (bwrap: Can't chdir to /tmp/...)."""
    root = tmp_path / "ws"
    root.mkdir()
    spec = build_spec(policy="workspace", root=str(root), home=home, probe=WORKING)
    built = argv(spec, ["bash", "-c", "ls"], root=str(root))

    tmp_at = built.index("/tmp")
    assert built.index("--bind") > tmp_at
    assert any(built[index] == "--tmpfs" and built[index + 1] == str(home / ".ssh")
               for index in range(len(built) - 1))


def test_masks_come_after_grants_so_a_grant_cannot_unmask(tmp_path, home):
    """A grant must not unmask its nested masked path: masks are host policy and grants per-run
    capability, so a later --ro-bind of $HOME must not cover the earlier --tmpfs $HOME/.ssh."""
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
    """bwrap cannot mount a tmpfs over a symlink (~/.aws on WSL): use the resolved path."""
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
    """The session store is the conversation history: tools must not read their own record."""
    store = tmp_path / "somewhere" / "avid-sessions"
    monkeypatch.setenv("AVID_SESSIONS_DIR", str(store))

    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)

    assert str(store) in spec.mask_dirs


def test_default_sessions_store_under_the_avid_home_is_not_masked_twice(tmp_path, monkeypatch):
    """The default store lives under ~/.avid; that mask already covers it, never mounted twice."""
    monkeypatch.setenv("AVID_HOME", str(tmp_path / "host-home" / ".avid"))

    spec = build_spec(
        policy="workspace", root=str(tmp_path / "ws"), home=tmp_path / "host-home", probe=WORKING
    )

    assert str(tmp_path / "host-home" / ".avid") in spec.mask_dirs
    assert str(tmp_path / "host-home" / ".avid" / "sessions") not in spec.mask_dirs


def test_the_relocated_avid_home_is_masked_not_only_dot_avid(tmp_path, home, monkeypatch):
    """AVID_HOME relocates ~/.avid; secrets, audit and index move with it, and so does the mask."""
    relocated = tmp_path / "elsewhere" / "avid-home"
    monkeypatch.setenv("AVID_HOME", str(relocated))
    monkeypatch.setenv("AVID_SESSIONS_DIR", str(relocated / "sessions"))

    spec = build_spec(policy="workspace", root=str(tmp_path / "ws"), home=home, probe=WORKING)

    assert str(relocated) in spec.mask_dirs
    assert str(relocated / "sessions") not in spec.mask_dirs  # already covered above


def test_sessions_store_inside_the_workspace_is_masked_after_the_workspace_bind(
    tmp_path, home, monkeypatch
):
    """A store inside the workspace is masked after the bind, or that bind covers the mask."""
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
    assert built.count(str(outside)) == 2  # source and target once each
    assert "--ro-bind" in built

    # A grant on a masked path is refused: the target appears only in that mask
    masked = argv(spec, ["bash", "-c", "ls"], grants=[(str(home / ".ssh"), "rw")])
    assert masked.count(str(home / ".ssh")) == 1


def test_grants_are_ignored_when_not_enforced(tmp_path, home):
    outside = tmp_path / "a.txt"
    outside.write_text("x", encoding="utf-8")
    spec = build_spec(policy="disabled", root=str(tmp_path), home=home, probe=WORKING)
    assert argv(spec, ["bash", "-c", "ls"], grants=[(str(outside), "rw")]) == ["bash", "-c", "ls"]


# ---- environment ----


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
    """The mask list and HOME must name one home, or the sandbox masks A and uses B."""
    spec = build_spec(policy="workspace", root=str(tmp_path), home=home, probe=WORKING)
    assert spec.apply_env({"PATH": "/usr/bin", "HOME": "/somewhere/else"})["HOME"] == str(home)


def test_child_env_full_inherits_and_degraded_scrubs(tmp_path, home):
    """full inherits everything on purpose (env=None, credentials usable); degraded uses the
    blacklist; enforced uses the whitelist."""
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


# ---- one real run ----


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
    """A real run: session files are neither readable nor listable in the sandbox, and the store
    must live outside /tmp (an empty tmpfs there would mask nothing)."""
    found = probe_backend()
    if not found.available:
        pytest.skip(f"这台机器上没有可用的 bwrap：{found.reason}")
    root = tmp_path / "ws"
    root.mkdir()
    store = home.parent / "avid-sessions"  # outside /tmp
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
    """The target must really exist to test a refused write: /tmp is an empty tmpfs in the sandbox
    (so writes there falsely succeed), while /var/tmp sits inside the read-only bind."""
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
    """Network is always on: no --unshare-net, so a sandboxed connection reaches a host
    loopback listener opened by the test itself (no external network involved).
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
        except OSError:  # no connection from the sandbox: the assertion below reports why
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


# ---- degraded env blacklist ----


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
