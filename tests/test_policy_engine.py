"""权限轻量化（阶段 51）的决策规格：默认直接跑、毁灭级双确认、凭据拒读、full 显式授权。

这里的每一行都是产品规格的一句话：默认形态下只有毁灭级命令会 (被) 问人；凭据拒读是
唯一硬拒（连 full 也拒）；区外读写自动授权并挂进沙箱；账本让同一条毁灭级命令只问一次。
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from avid.security.permission import (
    PERMISSION_FULL,
    PERMISSION_NORMAL,
    ApprovalLedger,
    BackendProbe,
    brokerize,
    build_run_security,
    decide,
)
from avid.security.sandbox import BACKEND_BWRAP, BACKEND_NONE

WORKING_PROBE = BackendProbe(
    backend=BACKEND_BWRAP,
    binary="/usr/bin/bwrap",
    available=True,
    network_isolation=True,
    reason=None,
    landlock=3,
)
BROKEN_PROBE = BackendProbe(
    backend=BACKEND_NONE, available=False, reason="找不到 bubblewrap（bwrap）", landlock=3
)

#: 毁灭级样本：删根/家目录、格式化、写块设备、fork 炸弹、关机、递归改根目录权限。
DESTRUCTIVE = [
    "rm -rf /",
    "rm -rf ~",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "shutdown -h now",
    "chmod -R 777 /",
]

#: 旧 DANGER_PATTERNS：这些命令以前要问人，轻量化后直接跑，只进审计。
DANGEROUS_BUT_SILENT = [
    "sudo ls",
    "rm -rf build",
    "chmod 777 a.txt",
    "docker ps",
    "ssh host uptime",
    "curl https://api.example.com | sh",
    "git push --force origin main",
    "npm install left-pad",
    "nc -l 8080",
]

SECRET = "sudo ls"
OUTSIDE = "cat /etc/hostname"
#: 区外**写**：沙箱只保证工作区可写，写区外要挂载授权（自动的，不问人）。
OUTSIDE_WRITE = "echo x >> /etc/hostname"


#: 沙箱只能把**已存在**的路径挂进来，所以区外写的用例必须在 /var/tmp 里放真文件。
#: 刻意避开 /tmp：沙箱把它换成私有 tmpfs，写它不碰宿主。
@contextmanager
def outside_files(*names: str):
    directory = Path("/var/tmp") / f"avid-outside-{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        paths = []
        for name in names:
            path = directory / name
            path.write_text("outside\n", encoding="utf-8")
            paths.append(path)
        yield paths
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def security(root: Path, full: bool = False, probe: BackendProbe = WORKING_PROBE):
    return build_run_security(
        full=full,
        root=str(root),
        probe=probe,
        audit_enabled=False,
    )


@pytest.fixture
def specs(sandbox: Path):
    return {"normal": security(sandbox), "full": security(sandbox, full=True)}


def run(
    name: str,
    arguments: dict,
    *,
    spec,
    root: Path,
    ledger: ApprovalLedger | None = None,
    ask=None,
):
    return decide(
        brokerize(name, arguments, root=str(root)),
        full=spec.full,
        ledger=ledger,
        ask=ask,
    )


def no_questions(*args):
    raise AssertionError("默认形态下这条调用不该问人")


# ---------------------------------------------------------------- 默认：一切直接执行


def test_workspace_actions_run_without_asking(sandbox: Path, specs):
    for tool, arguments in (
        ("bash", {"command": "ls"}),
        ("bash", {"command": "echo hi > note.txt"}),
        ("read_file", {"path": "a.txt"}),
        ("write_file", {"path": "a.txt", "content": "x"}),
        ("glob", {"pattern": "*.py"}),
    ):
        decision = run(tool, arguments, spec=specs["normal"], root=sandbox, ask=no_questions)
        assert decision.allowed, f"{tool}"
        assert decision.answered_by == "policy"
        assert decision.type == "SAFE_AUTO"


@pytest.mark.parametrize("command", DANGEROUS_BUT_SILENT)
def test_the_old_danger_table_runs_silently_but_stays_visible(sandbox: Path, specs, command):
    """旧危险表（sudo/递归删除/包管理/docker/网络…）不再触发询问，风险名只进审计。"""
    decision = run("bash", {"command": command}, spec=specs["normal"], root=sandbox, ask=no_questions)
    assert decision.allowed and decision.answered_by == "policy"

    action = brokerize("bash", {"command": command}, root=str(sandbox))
    assert action.damage is None, command
    # 事实仍在：要么有风险名，要么有网络/写这类可审计的能力标记。
    assert action.risks or action.network or action.capabilities, command


def test_outside_reads_and_writes_run_directly(sandbox: Path, specs):
    ledger = ApprovalLedger()
    for tool, arguments in (
        ("bash", {"command": OUTSIDE}),
        ("read_file", {"path": "/etc/hostname"}),
        ("glob", {"pattern": "*", "path": "/etc"}),
    ):
        decision = run(
            tool, arguments, spec=specs["normal"], root=sandbox, ledger=ledger, ask=no_questions
        )
        assert decision.allowed and decision.answered_by == "policy"
    # 只读的区外访问不记账（沙箱本来就给整个文件系统只读）
    assert ledger.path_grants() == ()


def test_outside_writes_are_granted_on_the_way_through(sandbox: Path, specs):
    """区外写不再问人：账本自动记授权，沙箱 argv 据此挂载。"""
    with outside_files("auto.txt") as (outside,):
        ledger = ApprovalLedger()
        decision = run(
            "bash",
            {"command": f"echo x >> {outside}"},
            spec=specs["normal"],
            root=sandbox,
            ledger=ledger,
            ask=no_questions,
        )
        assert decision.allowed and decision.answered_by == "policy"
        assert decision.grants == ((str(outside), "rw"),)
        assert ledger.path_grants() == ((str(outside), "rw"),)


def test_external_source_is_not_a_write_capability(sandbox: Path, specs):
    """外部源只读、写入工作区：不能把全命令的 write 误归到只读源上。"""
    for command in ("cp /etc/hostname local.txt", "cat /etc/hostname > local.txt"):
        decision = run("bash", {"command": command}, spec=specs["normal"], root=sandbox)
        assert decision.allowed and not decision.grants, command


def test_mcp_tools_and_subagents_run_directly(sandbox: Path, specs):
    """MCP 工具（用户自己装的 server）与 subagent 都不再审查。"""
    for tool, arguments in (
        ("mcp__demo__search", {"query": "x"}),
        ("subagent", {}),
    ):
        decision = run(tool, arguments, spec=specs["normal"], root=sandbox, ask=no_questions)
        assert decision.allowed and decision.answered_by == "policy", tool


# ---------------------------------------------------------------- 毁灭级：问一次


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_commands_ask_once_and_run_when_answered(sandbox: Path, specs, command):
    asked: list[str] = []

    def ask(name, arguments, reason):
        asked.append(reason)
        return True

    decision = run(
        "bash", {"command": command}, spec=specs["normal"], root=sandbox, ask=ask
    )
    assert decision.allowed and decision.answered_by == "user", command
    assert decision.kind == "danger"
    assert asked, command


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_commands_are_denied_without_an_ask_channel(sandbox: Path, specs, command):
    """确认不可能发生就不执行：没有询问通道时 fail closed。"""
    decision = run("bash", {"command": command}, spec=specs["normal"], root=sandbox)
    assert not decision.allowed, command
    assert (decision.kind, decision.answered_by) == ("danger", "policy")
    assert "没有可用的询问通道" in decision.message
    assert decision.type == "POLICY_DENIED"


def test_refusing_the_question_denies_with_its_own_guidance(sandbox: Path, specs):
    decision = run(
        "bash",
        {"command": "rm -rf /"},
        spec=specs["normal"],
        root=sandbox,
        ask=lambda *args: False,
    )
    assert not decision.allowed
    assert (decision.kind, decision.answered_by) == ("danger", "user")
    assert "不要重复提交同一条命令" in decision.message
    assert decision.type == "NEEDS_APPROVAL"


def test_one_answer_covers_the_rest_of_the_run(sandbox: Path, specs):
    """同意一次即生效：同一条规范化命令不再问（双确认只发生一次）。"""
    ledger = ApprovalLedger()
    asked: list[str] = []

    def ask(name, arguments, reason):
        asked.append(reason)
        return True

    first = run(
        "bash", {"command": "rm -rf /"}, spec=specs["normal"], root=sandbox, ledger=ledger, ask=ask
    )
    assert first.allowed and first.answered_by == "user"

    again = run(
        "bash",
        {"command": "rm -rf /"},
        spec=specs["normal"],
        root=sandbox,
        ledger=ledger,
        ask=no_questions,
    )
    assert again.allowed and again.answered_by == "ledger"
    assert len(asked) == 1


def test_different_destructive_commands_ask_separately(sandbox: Path, specs):
    ledger = ApprovalLedger()
    run(
        "bash",
        {"command": "rm -rf /"},
        spec=specs["normal"],
        root=sandbox,
        ledger=ledger,
        ask=lambda *args: True,
    )
    other = run(
        "bash",
        {"command": "shutdown -h now"},
        spec=specs["normal"],
        root=sandbox,
        ledger=ledger,
        ask=lambda *args: False,
    )
    assert not other.allowed


def test_full_skips_the_question_entirely(sandbox: Path, specs):
    decision = run(
        "bash", {"command": "rm -rf /"}, spec=specs["full"], root=sandbox, ask=no_questions
    )
    assert decision.allowed and decision.answered_by == "full"
    assert specs["full"].permission_mode == PERMISSION_FULL


def test_decision_exposes_structured_command_result_types(sandbox: Path, specs):
    """机器调用方不能只靠 bool 区分策略拒绝与需要授权。"""
    safe = run("bash", {"command": "ls"}, spec=specs["normal"], root=sandbox)
    assert safe.type == "SAFE_AUTO"

    policy = run("read_file", {"path": "/etc/shadow"}, spec=specs["normal"], root=sandbox)
    assert policy.type == "POLICY_DENIED"

    refused = run(
        "bash", {"command": "rm -rf /"}, spec=specs["normal"], root=sandbox, ask=lambda *a: False
    )
    assert refused.type == "NEEDS_APPROVAL"


def test_messages_say_what_the_model_should_do_next(sandbox: Path, specs):
    credential = run("read_file", {"path": "/etc/shadow"}, spec=specs["normal"], root=sandbox)
    unanswered = run("bash", {"command": "rm -rf /"}, spec=specs["normal"], root=sandbox)
    refused = run(
        "bash", {"command": "rm -rf /"}, spec=specs["normal"], root=sandbox, ask=lambda *a: False
    )

    assert "任何确认都无效" in credential.message
    assert "没有可用的询问通道" in unanswered.message
    assert "不要重复提交同一条命令" in refused.message
    assert len({credential.message, unanswered.message, refused.message}) == 3


# ---------------------------------------------------------------- 凭据拒读：唯一硬拒


def test_credentials_are_refused_in_every_shape(sandbox: Path, specs):
    """凭据进上下文不可撤回：ask 答应、账本预先批准、full 都不放行。"""
    ledger = ApprovalLedger()
    ledger.remember(("path", "/etc/shadow", "ro"))

    for spec in specs.values():
        for tool, arguments in (
            ("read_file", {"path": "/etc/shadow"}),
            ("bash", {"command": "cat /etc/shadow"}),
        ):
            decision = run(
                tool,
                arguments,
                spec=spec,
                root=sandbox,
                ledger=ledger,
                ask=lambda *args: True,
            )
            assert not decision.allowed, f"{tool} / {spec.permission_mode}"
            assert decision.kind == "credential"
            assert "受保护的宿主资源" in decision.message


@pytest.mark.parametrize(
    "path",
    ["~/.ssh/id_rsa", "~/.aws/credentials", "~/.gnupg/secring.gpg", "/etc/sudoers", "/root/.bashrc", "key.pem"],
)
def test_credential_paths_cover_host_secrets(sandbox: Path, specs, path):
    decision = run("read_file", {"path": path}, spec=specs["normal"], root=sandbox)
    assert not decision.allowed, path
    assert decision.kind == "credential"


def test_file_tools_allow_outside_paths_but_refuse_credentials(sandbox: Path, specs):
    """文件工具的闸门只剩凭据拒读；区外写不再需要授权。"""
    from avid.agent.state import RunState
    from avid.agent.tools.files import write_file

    with outside_files("tool-write.txt") as (outside,):
        state = RunState.for_run(security=security(sandbox), workspace_root=str(sandbox))
        written = write_file({"path": str(outside), "content": "x"}, state=state)
        assert "拒绝访问" not in written
        assert outside.read_text() == "x"

    state = RunState.for_run(security=security(sandbox), workspace_root=str(sandbox))
    refused = write_file({"path": "~/.ssh/authorized_keys", "content": "x"}, state=state)
    assert "受保护的宿主资源" in refused


# ---------------------------------------------------------------- 沙箱与规格


def test_normal_runs_inside_the_workspace_sandbox(sandbox: Path, specs):
    normal = specs["normal"]
    assert normal.sandbox.enforced
    assert normal.permission_mode == PERMISSION_NORMAL
    assert normal.summary()["permission"] == "normal"


def test_full_disables_the_sandbox(sandbox: Path, specs):
    full = specs["full"]
    assert not full.sandbox.enforced
    assert full.sandbox.policy == "disabled"
    assert full.summary()["permission"] == "full"


def test_degraded_sandbox_does_not_ask(sandbox: Path):
    """沙箱是纵深不是门槛：后端不可用也不把命令推回给人（毁灭级照旧问）。"""
    degraded = security(sandbox, probe=BROKEN_PROBE)

    listed = run("bash", {"command": "ls"}, spec=degraded, root=sandbox, ask=no_questions)
    assert listed.allowed and listed.answered_by == "policy"

    with outside_files("degraded.txt") as (outside,):
        written = run(
            "bash",
            {"command": f"echo x >> {outside}"},
            spec=degraded,
            root=sandbox,
            ask=no_questions,
        )
        assert written.allowed

    asked: list[str] = []
    destructive = run(
        "bash",
        {"command": "rm -rf /"},
        spec=degraded,
        root=sandbox,
        ask=lambda name, arguments, reason: asked.append(reason) or True,
    )
    assert destructive.allowed and asked


def test_degraded_state_is_visible_in_the_spec(sandbox: Path):
    spec = security(sandbox, probe=BROKEN_PROBE)
    summary = spec.summary()
    assert summary["sandbox_state"]["degraded"] is True
    assert summary["sandbox_state"]["enforced"] is False
    assert summary["sandbox_state"]["reason"]


# ---------------------------------------------------------------- 能力账本


def test_the_ledger_keys_destructive_commands_by_normalized_text(sandbox: Path, specs):
    ledger = ApprovalLedger()
    run(
        "bash",
        {"command": "rm   -rf   /"},
        spec=specs["normal"],
        root=sandbox,
        ledger=ledger,
        ask=lambda *args: True,
    )
    # 规范化之后同一条命令再次到来时命中账本
    again = run(
        "bash",
        {"command": "rm -rf /"},
        spec=specs["normal"],
        root=sandbox,
        ledger=ledger,
        ask=no_questions,
    )
    assert again.answered_by == "ledger"


def test_path_grants_are_rw_and_win_over_ro(sandbox: Path, specs):
    ledger = ApprovalLedger()
    ledger.remember(("path", "/tmp/out.txt", "ro"))
    ledger.remember(("path", "/tmp/out.txt", "rw"))
    assert ledger.path_grants() == (("/tmp/out.txt", "rw"),)


def test_every_allow_records_its_path_grants(sandbox: Path, specs):
    """每一条允许都要记账：毁灭级放行的命令要写区外时，缺了挂载会在沙箱里撞上只读。"""
    with outside_files("destructive.txt") as (outside,):
        ledger = ApprovalLedger()
        decision = run(
            "bash",
            {"command": f"rm -rf {outside}"},
            spec=specs["normal"],
            root=sandbox,
            ledger=ledger,
            ask=lambda *args: True,
        )
        assert decision.allowed
        assert ledger.path_grants() == ((str(outside), "rw"),)

        full_ledger = ApprovalLedger()
        run(
            "bash",
            {"command": f"rm -rf {outside}"},
            spec=specs["full"],
            root=sandbox,
            ledger=full_ledger,
        )
        assert full_ledger.path_grants() == ((str(outside), "rw"),)


def test_grants_only_mount_the_write_destination(sandbox: Path, specs):
    """外部只读源不能顺带挂成可写：grants 只有写目标。"""
    with outside_files("source.txt", "destination.txt") as (source, destination):
        ledger = ApprovalLedger()
        decision = run(
            "bash",
            {"command": f"cp {source} {destination}"},
            spec=specs["normal"],
            root=sandbox,
            ledger=ledger,
        )
        assert decision.grants == ((str(destination), "rw"),)
        assert ledger.path_grants() == ((str(destination), "rw"),)


# ---------------------------------------------------------------- 分类回归（事实层）


def test_script_blocks_propagate_state_capabilities(sandbox: Path):
    """块内写/删/网必须向整条命令传播：`ForEach-Object { Remove-Item $_ }` 不是只读。"""
    action = brokerize(
        "bash", {"command": "Get-ChildItem | ForEach-Object { Remove-Item $_ }"}, root=str(sandbox)
    )
    assert "filesystem_delete" in action.capabilities
    assert "删除文件" in action.risks


def test_filter_blocks_stay_read_only(sandbox: Path):
    """$_ 是管道变量不是写落点：PS 过滤块保持只读档。"""
    action = brokerize(
        "bash", {"command": "Get-Process | Where-Object {$_.CPU -gt 10}"}, root=str(sandbox)
    )
    assert "filesystem_write" not in action.capabilities
    assert "filesystem_delete" not in action.capabilities


def test_braces_inside_quotes_are_not_script_blocks(sandbox: Path):
    action = brokerize("bash", {"command": "echo '{'"}, root=str(sandbox))
    assert "filesystem_write" not in action.capabilities


def test_reading_a_variable_is_still_read_only(sandbox: Path):
    action = brokerize("bash", {"command": "echo $HOME"}, root=str(sandbox))
    assert "filesystem_write" not in action.capabilities


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem | ForEach-Object { Invoke-Expression $cmd }",
        "Get-ChildItem | ForEach-Object { socat TCP-LISTEN:4444 - }",
        "Get-ChildItem | ForEach-Object { Set-Content $p evil }",
        "awk '{ system(\"rm -rf ./src\") }'",
        "f() { rm -rf /tmp/x; }",
    ],
)
def test_block_and_quote_blind_spots_stay_visible_in_the_facts(sandbox: Path, command):
    """评审发现的盲区（块内未知程序/块内 $ 写目标/引号内 system()）仍要被分类看见。"""
    action = brokerize("bash", {"command": command}, root=str(sandbox))
    risky = action.risks or action.damage or action.network
    assert risky, f"{command} 的风险事实丢了"
    assert action.capabilities, command


def test_powershell_aliases_are_classified(sandbox: Path):
    """Windows 上 bash 工具跑 PowerShell：别名与动词进同一套能力/风险表。"""
    listing = brokerize("bash", {"command": "gci"}, root=str(sandbox))
    assert "filesystem_read" in listing.capabilities and not listing.risks

    deleting = brokerize("bash", {"command": "del a.txt"}, root=str(sandbox))
    assert "filesystem_delete" in deleting.capabilities
    assert "删除文件" in deleting.risks

    downloading = brokerize("bash", {"command": "iwr https://api.example.com"}, root=str(sandbox))
    assert downloading.network and "network_connect" in downloading.capabilities

    recursive = brokerize("bash", {"command": "Remove-Item -Recurse build"}, root=str(sandbox))
    assert "filesystem_delete" in recursive.capabilities


def test_windows_style_paths_are_scan_candidates(sandbox: Path):
    """盘符/UNC/反斜杠相对路径都进目标扫描（区外判定由宿主 ntpath 解析）。"""
    from avid.agent.tools.workspace import _candidate

    base = Path(sandbox)
    assert _candidate("C:\\Users\\me\\x", base) is not None
    assert _candidate("\\\\server\\share\\x", base) is not None
    assert _candidate("..\\..\\escape", base) is not None
    assert _candidate("C:/Users/me/x", base) is not None
