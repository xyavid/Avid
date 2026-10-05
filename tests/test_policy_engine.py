"""Policy Engine：三模式 × 动作类别的决策表、能力账本、分类器、失败关闭。

这里的每一行都是产品规格的一句话：谁回答 REVIEW（人 / 分类器 / 无人）、
沙箱能保证的事不打搅人、降级不静默、deny 连 full 也不放行。
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from avid.security.permission import (
    APPROVAL_CLASSIFIER,
    APPROVAL_NONE,
    APPROVAL_USER,
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

SECRET = "sudo ls"
OUTSIDE = "cat /etc/hostname"
#: 区外**写**：这才是越过沙箱的动作（沙箱只保证工作区可写）。只做裁决、不执行。
OUTSIDE_WRITE = "echo x >> /etc/hostname"
HARD = "rm -rf /"

#: 沙箱只能把**已存在**的路径挂进来，所以区外写的用例必须在 /var/tmp 里放真文件。
#: 刻意避开 /tmp：沙箱把它换成私有 tmpfs，写它不碰宿主，因此不算越过沙箱。
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



def security(root: Path, mode: str, probe: BackendProbe = WORKING_PROBE, **kwargs):
    return build_run_security(
        mode=mode,
        root=str(root),
        probe=probe,
        audit_enabled=False,
        full_ack=mode == "full",
        **kwargs,
    )


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
        mode=spec.mode,
        ladder=spec.ladder,
        sandbox=spec.sandbox,
        ledger=ledger,
        ask=ask,
    )


@pytest.fixture
def specs(sandbox: Path):
    return {mode: security(sandbox, mode) for mode in ("manual", "auto", "full")}


# ---------------------------------------------------------------- 三轴回答 REVIEW


def test_manual_asks_and_records_the_answer(sandbox: Path, specs):
    ledger = ApprovalLedger()
    asked: list[tuple[str, str]] = []

    def ask(name, arguments, reason):
        asked.append((name, reason))
        return True

    decision = run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask)
    assert decision.allowed and decision.answered_by == "user"
    assert asked == [("bash", "提权")]

    # 同意一次即生效：同一条命令不再问
    again = run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask)
    assert again.allowed and again.answered_by == "ledger"
    assert len(asked) == 1


def test_manual_denies_when_no_answerer_is_available(sandbox: Path, specs):
    decision = run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox)
    assert not decision.allowed
    assert (decision.kind, decision.answered_by) == ("danger", "user")


def test_manual_asks_once_per_path_not_per_command(sandbox: Path, specs):
    """越过沙箱的写按**路径**记账：批准 `a` 不表示批准 `b`。"""
    with outside_files("a.txt", "b.txt") as (first, second):
        ledger = ApprovalLedger()
        asked: list[str] = []

        def ask(name, arguments, reason):
            asked.append(reason)
            return True

        command = f"echo x >> {first}"
        accepted = run(
            "bash", {"command": command}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask
        )
        assert accepted.allowed and accepted.grants == ((str(first), "rw"),)
        assert len(asked) == 1

        # 同一条命令再来一次：账本命中，不再问
        again = run(
            "bash", {"command": command}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask
        )
        assert again.allowed and again.answered_by == "ledger"
        assert len(asked) == 1

        # 换一个区外目标：重新问
        other = run(
            "bash",
            {"command": f"echo x >> {second}"},
            spec=specs["manual"],
            root=sandbox,
            ledger=ledger,
            ask=ask,
        )
        assert other.allowed and len(asked) == 2



def test_auto_asks_the_user_when_one_is_reachable(sandbox: Path, specs):
    """auto 的 REVIEW 交人：有人答就由人定，账本记一次——「判不准即拒」的旧口径已废。"""
    ledger = ApprovalLedger()
    asked: list[str] = []

    decision = run(
        "bash",
        {"command": SECRET},
        spec=specs["auto"],
        root=sandbox,
        ledger=ledger,
        ask=lambda name, arguments, reason: asked.append(reason) or True,
    )
    assert decision.allowed and decision.answered_by == "user"
    assert asked == ["提权"]
    assert specs["auto"].approval == APPROVAL_CLASSIFIER

    # 同意一次即生效：同一条命令不再问
    again = run("bash", {"command": SECRET}, spec=specs["auto"], root=sandbox, ledger=ledger)
    assert again.allowed and again.answered_by == "ledger"


def test_auto_denies_when_nobody_can_answer(sandbox: Path, specs):
    """无人可问才 fail closed——这是 auto 与 manual 的唯一判别差异。"""
    decision = run("bash", {"command": SECRET}, spec=specs["auto"], root=sandbox)
    assert not decision.allowed
    assert (decision.kind, decision.answered_by) == ("danger", "classifier")
    assert "没有可用的询问通道" in decision.message


def test_full_allows_without_asking_but_records_it(sandbox: Path, specs):
    decision = run("bash", {"command": SECRET}, spec=specs["full"], root=sandbox)
    assert decision.allowed and decision.answered_by == "none"
    assert specs["full"].approval == APPROVAL_NONE


def test_decision_exposes_structured_command_result_types(sandbox: Path, specs):
    """机器调用方不能只靠 bool 区分策略拒绝与需要授权。"""
    safe = run("bash", {"command": "ls"}, spec=specs["manual"], root=sandbox)
    assert safe.type == "SAFE_AUTO"

    policy = run("read_file", {"path": "/etc/shadow"}, spec=specs["manual"], root=sandbox)
    assert policy.type == "POLICY_DENIED"

    approval = run("bash", {"command": "sudo ls"}, spec=specs["manual"], root=sandbox)
    assert approval.type == "NEEDS_APPROVAL"

    classifier = run("bash", {"command": "sudo ls"}, spec=specs["auto"], root=sandbox)
    assert classifier.type == "POLICY_DENIED"


def test_unmountable_outside_target_is_sandbox_denied(sandbox: Path, specs):
    missing = Path("/var/tmp") / f"avid-missing-{sandbox.name}.txt"
    decision = run("bash", {"command": f"echo hi > {missing}"}, spec=specs["manual"], root=sandbox, ask=lambda *a: True)
    assert decision.type == "SANDBOX_DENIED"
    assert decision.operation == "filesystem_write"
    assert decision.target == str(missing)


def test_network_listen_reports_separate_operation(sandbox: Path, specs):
    decision = run("bash", {"command": "nc -l 8080"}, spec=specs["auto"], root=sandbox)
    assert (decision.type, decision.operation) == ("SANDBOX_DENIED", "network_listen")


def test_restricted_network_is_a_sandbox_denial_not_a_command_failure(sandbox: Path, specs):
    decision = run("bash", {"command": "curl https://api.example.com"}, spec=specs["manual"], root=sandbox)
    assert decision.type == "SANDBOX_DENIED"
    assert decision.code == "SANDBOX_NETWORK_DENIED"
    assert decision.operation == "network_connect"
    assert decision.target == "api.example.com"

    full = run("bash", {"command": "curl https://api.example.com"}, spec=specs["full"], root=sandbox)
    assert full.allowed



@pytest.mark.parametrize("command", [
    "rm a.txt", "git commit -m message", "git restore a.txt", "git reset HEAD~1",
    "find . -exec touch x \\;", "python -c 'print(1)'",
])
def test_risky_argv_never_becomes_safe_auto(sandbox: Path, specs, command):
    manual = run("bash", {"command": command}, spec=specs["manual"], root=sandbox)
    assert manual.type == "NEEDS_APPROVAL"
    auto = run("bash", {"command": command}, spec=specs["auto"], root=sandbox)
    assert auto.type == "POLICY_DENIED"


#: 三轴各自的出口在这一张表里一览：同一件事在 manual 是"问不到就拒"、auto 是"分类器
#: 判死"、full 是"没人拦"，而 deny 那一档三种模式逐字相同。
TABLE = [
    # (说明, 工具, 参数, manual, auto, full)
    ("硬拒绝", "bash", {"command": HARD}, ("deny", "hard"), ("deny", "hard"), ("deny", "hard")),
    (
        "ADMIN 凭据",
        "read_file",
        {"path": "/etc/shadow"},
        ("deny", "credential"),
        ("deny", "credential"),
        ("deny", "credential"),
    ),
    (
        "PROJECT deny",
        "write_file",
        {"path": ".git/hooks/pre-commit", "content": "x"},
        ("deny", "rule"),
        ("deny", "rule"),
        ("deny", "rule"),
    ),
    # 放行的行也保留 kind：它记的是"**因为什么**被审过"，审计与事件都要这个信息。
    ("区外只读（沙箱已保证）", "bash", {"command": OUTSIDE}, ("allow", ""), ("allow", ""), ("allow", "")),
    ("越过沙箱（写区外）", "bash", {"command": OUTSIDE_WRITE}, ("deny", "outside"), ("deny", "outside"), ("allow", "outside")),

    ("危险", "bash", {"command": SECRET}, ("deny", "danger"), ("deny", "danger"), ("allow", "danger")),
    ("成本", "subagent", {}, ("deny", "cost"), ("allow", "cost"), ("allow", "cost")),
    ("区内只读", "read_file", {"path": "a.txt"}, ("allow", ""), ("allow", ""), ("allow", "")),
    ("区内常规命令", "bash", {"command": "ls"}, ("allow", ""), ("allow", ""), ("allow", "")),
]


@pytest.mark.parametrize(
    ("label", "tool", "arguments", "manual", "auto", "full"),
    TABLE,
    ids=[row[0] for row in TABLE],
)
def test_decision_table(sandbox: Path, specs, label, tool, arguments, manual, auto, full):
    expected = {"manual": manual, "auto": auto, "full": full}
    for mode, want in expected.items():
        decision = run(tool, arguments, spec=specs[mode], root=sandbox)
        assert (decision.verdict, decision.kind) == want, f"{label} / {mode}"


#: REVIEW 的四类理由。manual 下它们会变成一次询问；auto 下由分类器判；full 直接放行。
REVIEW_TABLE = [
    ("越过沙箱（写区外）", "bash", {"command": OUTSIDE_WRITE}, "outside"),

    ("危险", "bash", {"command": SECRET}, "danger"),
    ("成本", "subagent", {}, "cost"),
    ("ask 规则", "read_file", {"path": ".env"}, "rule"),
]


@pytest.mark.parametrize(
    ("label", "tool", "arguments", "kind"), REVIEW_TABLE, ids=[row[0] for row in REVIEW_TABLE]
)
def test_manual_review_becomes_a_prompt(sandbox: Path, specs, label, tool, arguments, kind):
    """REVIEW 在 manual 下真的问人，并且**理由分类原样保留**（事件与审计都要它）。"""
    asked: list[str] = []
    decision = run(
        tool,
        arguments,
        spec=specs["manual"],
        root=sandbox,
        ledger=ApprovalLedger(),
        ask=lambda name, arguments, reason: asked.append(reason) or True,
    )
    assert decision.allowed and decision.answered_by == "user"
    assert decision.kind == kind
    assert asked, f"{label} 没有发起询问"


@pytest.mark.parametrize(
    ("label", "tool", "arguments", "kind"), REVIEW_TABLE, ids=[row[0] for row in REVIEW_TABLE]
)
def test_auto_review_reaches_the_user(sandbox: Path, specs, label, tool, arguments, kind):
    """auto 把同一批 REVIEW 交给用户：有人可问就问（kind 原样保留），无人可问才拒。

    「成本」是例外：subagent 的成本档不是安全审查，auto 仍由分类器直接放行。
    """
    if kind == "cost":
        decision = run(tool, arguments, spec=specs["auto"], root=sandbox, ledger=ApprovalLedger())
        assert decision.allowed and decision.answered_by in {"classifier", "policy"}
        return

    asked: list[str] = []
    allowed = run(
        tool,
        arguments,
        spec=specs["auto"],
        root=sandbox,
        ledger=ApprovalLedger(),
        ask=lambda name, arguments, reason: asked.append(reason) or True,
    )
    assert allowed.allowed and allowed.answered_by == "user", label
    assert allowed.kind == kind
    assert asked, f"{label} 没有发起询问"

    # 干净账本 + 无应答者：分类器交不出去，按同一 kind 拒绝
    refused = run(tool, arguments, spec=specs["auto"], root=sandbox, ledger=ApprovalLedger())
    assert not refused.allowed and refused.kind == kind


@pytest.mark.parametrize(
    ("label", "tool", "arguments", "kind"), REVIEW_TABLE, ids=[row[0] for row in REVIEW_TABLE]
)
def test_full_allows_the_same_review_silently(sandbox: Path, specs, label, tool, arguments, kind):
    decision = run(tool, arguments, spec=specs["full"], root=sandbox, ledger=ApprovalLedger())
    assert decision.allowed and decision.answered_by == "none"


def test_workspace_actions_never_reach_review(sandbox: Path, specs):
    """manual 与 auto 的沙箱相同 → 同一批"区内常规动作"在三模式下都直接放行。

    这条是"沙箱让免问变安全"的机械证据：如果有人把 approval 与 sandbox 耦合成
    "manual 什么都问"，它会在 auto 那一列失败。
    """
    for mode in ("manual", "auto"):
        for tool, arguments in (
            ("bash", {"command": "ls"}),
            ("read_file", {"path": "a.txt"}),
            ("glob", {"pattern": "*.py"}),
        ):
            decision = run(tool, arguments, spec=specs[mode], root=sandbox)
            assert decision.allowed, f"{tool} / {mode}"
            assert decision.answered_by == "policy"


def test_ask_rules_are_review_in_every_mode(sandbox: Path, specs):
    """``.env`` 是 ask 档：manual 问、auto 拒、full 放行（连 full 也走同一条事实）。"""
    arguments = {"path": ".env"}

    manual = run("read_file", arguments, spec=specs["manual"], root=sandbox, ask=lambda *a: False)
    assert (manual.verdict, manual.kind) == ("deny", "rule")

    auto = run("read_file", arguments, spec=specs["auto"], root=sandbox)
    assert (auto.verdict, auto.kind, auto.answered_by) == ("deny", "rule", "classifier")

    full = run("read_file", arguments, spec=specs["full"], root=sandbox)
    assert full.allowed


def test_full_never_bypasses_the_ladder(sandbox: Path, specs):
    """产品口径：full 是"没有沙箱与人"，不是"没有规则"。"""
    for tool, arguments in (
        ("read_file", {"path": "/etc/shadow"}),
        ("write_file", {"path": ".git/hooks/pre-commit", "content": "x"}),
    ):
        decision = run(tool, arguments, spec=specs["full"], root=sandbox)
        assert decision.verdict == "deny"
        assert decision.answered_by == ""


# ---------------------------------------------------------------- 降级（不静默）


def test_degraded_sandbox_pushes_managed_tools_back_to_review(sandbox: Path):
    manual = security(sandbox, "manual", probe=BROKEN_PROBE)

    asked = []
    decision = run(
        "bash",
        {"command": "ls"},
        spec=manual,
        root=sandbox,
        ask=lambda name, arguments, reason: asked.append(reason) or True,
    )
    assert (decision.verdict, decision.kind) == ("allow", "degraded")
    assert asked and "沙箱不可用" in asked[0]

    # 区内只读不因为"没有沙箱"而多问一句（它的保证来自路径校验，不来自沙箱）
    assert run("read_file", {"path": "a.txt"}, spec=manual, root=sandbox).allowed


def test_auto_degraded_runs_proven_read_only_and_workspace_writes(sandbox: Path):
    """无沙箱平台的 auto 新阶梯：证明得了「只读」或「只写工作区」就裸跑。"""
    auto = security(sandbox, "auto", probe=BROKEN_PROBE)

    listed = run("bash", {"command": "ls"}, spec=auto, root=sandbox)
    assert listed.allowed and listed.answered_by == "classifier"

    written = run("bash", {"command": "echo hi > note.txt"}, spec=auto, root=sandbox)
    assert written.allowed and written.answered_by == "classifier"


def test_auto_degraded_asks_for_the_unproven(sandbox: Path):
    """证明不了的命令：有人可问就问，无人可问才拒——删除属于此类（不可逆）。"""
    auto = security(sandbox, "auto", probe=BROKEN_PROBE)

    assert not run("bash", {"command": "rm a.txt"}, spec=auto, root=sandbox).allowed
    assert not run("bash", {"command": "python -c 'print(1)'"}, spec=auto, root=sandbox).allowed

    asked = run(
        "bash",
        {"command": "rm a.txt"},
        spec=auto,
        root=sandbox,
        ledger=ApprovalLedger(),
        ask=lambda name, arguments, reason: True,
    )
    assert asked.allowed and asked.answered_by == "user"


def test_auto_degraded_network_becomes_a_review(sandbox: Path):
    """沙箱缺席时网络出口由人把守：问人而非物理拒绝（物理拒绝只在沙箱强制时成立）。"""
    manual = security(sandbox, "manual", probe=BROKEN_PROBE)
    auto = security(sandbox, "auto", probe=BROKEN_PROBE)
    command = {"command": "curl https://api.example.com"}

    asked = run("bash", command, spec=manual, root=sandbox, ask=lambda name, arguments, reason: True)
    assert (asked.verdict, asked.kind) == ("allow", "net_ask")

    assert not run("bash", command, spec=auto, root=sandbox).allowed
    auto_allowed = run(
        "bash", command, spec=auto, root=sandbox, ledger=ApprovalLedger(), ask=lambda *a: True
    )
    assert auto_allowed.allowed and auto_allowed.answered_by == "user"


def test_variable_targets_stay_unproven(sandbox: Path):
    """$VAR 的落点证明不了（可能指向任何地方）：含 $ 的命令不进 proven 档。"""
    auto = security(sandbox, "auto", probe=BROKEN_PROBE)
    decision = run("bash", {"command": "echo hi > $HOME/notes"}, spec=auto, root=sandbox)
    assert not decision.allowed


def test_powershell_commands_walk_the_same_ladder(sandbox: Path):
    """Windows 上 bash 工具跑 PowerShell：PS 动词表让它与 POSIX 同一套阶梯。"""
    auto = security(sandbox, "auto", probe=BROKEN_PROBE)

    assert run("bash", {"command": "Get-ChildItem"}, spec=auto, root=sandbox).allowed
    assert run("bash", {"command": "Get-Content .\\a.txt"}, spec=auto, root=sandbox).allowed
    assert run("bash", {"command": "Set-Location"}, spec=auto, root=sandbox).allowed

    # 网络类交人：无人可问 → 拒（与 curl 同口径）
    net = run(
        "bash", {"command": "Invoke-WebRequest https://api.example.com"}, spec=auto, root=sandbox
    )
    assert not net.allowed

    # 递归删除进危险档：有人可问就问，而不是判死
    asked = run(
        "bash",
        {"command": "Remove-Item -Recurse build"},
        spec=auto,
        root=sandbox,
        ledger=ApprovalLedger(),
        ask=lambda name, arguments, reason: True,
    )
    assert asked.allowed and asked.answered_by == "user"


def test_full_ignores_degradation(sandbox: Path):
    """full 本来就不要沙箱，所以"沙箱不可用"对它不是降级。"""
    full = security(sandbox, "full", probe=BROKEN_PROBE)
    assert run("bash", {"command": "ls"}, spec=full, root=sandbox).allowed


def test_degraded_state_is_visible_in_the_spec(sandbox: Path):
    spec = security(sandbox, "manual", probe=BROKEN_PROBE)
    summary = spec.summary()
    assert summary["sandbox_state"]["degraded"] is True
    assert summary["sandbox_state"]["enforced"] is False
    assert summary["sandbox_state"]["reason"]


# ---------------------------------------------------------------- 能力账本


def test_reading_outside_the_workspace_stays_inside_the_sandbox(sandbox: Path, specs):
    """沙箱以 ``--ro-bind / /`` 提供整个文件系统的只读访问：读区外是**已有能力**。

    边界不在"工作区"，而在"沙箱保证不了什么"：Codex 的 ``workspace-write``
    （"permits reading files, editing files in cwd and writable_roots"）与
    Claude Code 沙箱的 read / write 分层都是这个口径。
    """
    for mode in ("manual", "auto", "full"):
        for tool, arguments in (
            ("bash", {"command": OUTSIDE}),
            ("read_file", {"path": "/etc/hostname"}),
            ("glob", {"pattern": "*", "path": "/etc"}),
        ):
            decision = run(tool, arguments, spec=specs[mode], root=sandbox)
            assert decision.allowed, f"{tool} / {mode}"
            assert decision.answered_by == "policy"
            assert decision.type == "SAFE_AUTO"


def test_tmp_is_inside_the_sandbox_for_bash_but_not_for_file_tools(sandbox: Path, specs):
    """``bash`` 跑在沙箱里，宿主 /tmp 已被换成私有 tmpfs：写它碰不到宿主，不必问。
    文件工具在 agent 进程里跑，它的 /tmp 写会落到宿主，因此仍要授权。"""
    from avid.agent.state import RunState
    from avid.agent.tools.files import write_file

    decision = run("bash", {"command": "echo x > /tmp/avid-probe.txt"}, spec=specs["manual"], root=sandbox)
    assert decision.verdict == "allow" and decision.kind == ""

    state = RunState.for_run(security=specs["manual"], workspace_root=str(sandbox))
    assert "拒绝访问" in write_file({"path": "/tmp/avid-file-probe.txt", "content": "x"}, state=state)


def test_outside_source_is_not_a_write_capability(sandbox: Path, specs):
    """外部源只读、写入工作区：不能把全命令的 write 误归到只读源上。"""
    for command in ("cp /etc/hostname local.txt", "cat /etc/hostname > local.txt"):
        decision = run("bash", {"command": command}, spec=specs["manual"], root=sandbox)
        assert decision.allowed and not decision.grants, command


def test_approval_mounts_only_the_outside_write_destination(sandbox: Path, specs):
    """批准外部目标写入时，不能顺带把命令中的外部只读源挂成可写。"""
    with outside_files("source.txt", "destination.txt") as (source, destination):
        ledger = ApprovalLedger()
        decision = run(
            "bash",
            {"command": f"cp {source} {destination}"},
            spec=specs["manual"], root=sandbox, ledger=ledger, ask=lambda *args: True,
        )
        assert decision.grants == ((str(destination), "rw"),)
        assert ledger.path_grants() == ((str(destination), "rw"),)



    """只读授予不能给文件工具写权限：越出沙箱的写必须按 rw 口径批准。"""
    from avid.agent.state import RunState
    from avid.agent.tools.files import write_file

    with outside_files("write.txt") as (outside,):
        state = RunState.for_run(security=specs["manual"], workspace_root=str(sandbox))
        state.ledger.remember(("path", str(outside), "ro"))
        assert "拒绝访问" in write_file({"path": str(outside), "content": "x"}, state=state)
        assert outside.read_text() == "outside\n"


def test_grants_do_not_authorize_other_targets_or_upgrade_read_to_write(sandbox: Path, specs):
    """一个目标一次授权：只读授予不给写，多目标命令要**每个**目标都获准。"""
    with outside_files("a.txt", "b.txt") as (first, second):
        ledger = ApprovalLedger()
        ledger.remember(("path", str(first), "ro"))
        write_one = run(
            "bash", {"command": f"echo x >> {first}"}, spec=specs["manual"], root=sandbox, ledger=ledger
        )
        assert write_one.type == "NEEDS_APPROVAL"

        run(
            "bash",
            {"command": f"echo x >> {first}"},
            spec=specs["manual"],
            root=sandbox,
            ledger=ledger,
            ask=lambda *a: True,
        )
        multi = run(
            "bash",
            {"command": f"echo x >> {first} >> {second}"},
            spec=specs["manual"],
            root=sandbox,
            ledger=ledger,
        )
        assert multi.type == "NEEDS_APPROVAL"


def test_ledger_records_path_capabilities_with_access(sandbox: Path, specs):
    """区外**读**不写账本（沙箱已保证），只有越出沙箱的**写**才记能力。"""
    with outside_files("granted.txt") as (path,):
        ledger = ApprovalLedger()
        run("read_file", {"path": str(path)}, spec=specs["manual"], root=sandbox, ledger=ledger)
        assert ledger.path_grants() == ()

        run(
            "bash",
            {"command": f"echo x >> {path}"},
            spec=specs["manual"],
            root=sandbox,
            ledger=ledger,
            ask=lambda *a: True,
        )
        assert ledger.path_grants() == ((str(path), "rw"),)
        assert ledger.outside_allowed(str(path), "rw") is True
        assert ledger.outside_allowed(str(path), "ro") is True
        assert ledger.outside_allowed("/etc/shadow", "rw") is False



def test_write_grants_are_rw_and_win_over_ro(sandbox: Path, specs):
    ledger = ApprovalLedger()
    ledger.remember(("path", "/tmp/out.txt", "ro"))
    ledger.remember(("path", "/tmp/out.txt", "rw"))
    assert ledger.path_grants() == (("/tmp/out.txt", "rw"),)


def test_capability_lookup_ignores_the_access_suffix(sandbox: Path):
    ledger = ApprovalLedger()
    ledger.remember(("path", "/etc/hosts", "ro"))
    assert ledger.has_capability("path", "/etc/hosts") is True
    assert ledger.has_capability("path", "/etc/HOSTS") is False
    assert ledger.has_capability("command", "/etc/hosts") is False


def test_ledger_keys_are_capability_types(sandbox: Path, specs):
    """原则⑦：升级是授予能力（命令 / 路径 / 工具），不是"关沙箱"。"""
    ledger = ApprovalLedger()
    run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=lambda *a: True)
    run("subagent", {}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=lambda *a: True)
    assert ledger.has_capability("command", SECRET) is True
    assert ledger.has_capability("tool", "subagent") is True
    # 没有"关沙箱"这类能力：账本的键域就是能力域
    assert ledger.has_capability("sandbox", "disabled") is False
    assert len(ledger) == 2


def test_a_grant_does_not_leak_across_capability_types(sandbox: Path, specs):
    ledger = ApprovalLedger()
    ask = lambda *a: True  # noqa: E731
    run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask)
    # 同意过 `sudo ls` 不等于同意别的危险命令
    other = run(
        "bash", {"command": "chmod 777 a.txt"}, spec=specs["manual"], root=sandbox, ledger=ledger
    )
    assert not other.allowed


def test_hard_deny_is_identical_in_every_mode(sandbox: Path, specs):
    for mode, spec in specs.items():
        decision = run("bash", {"command": HARD}, spec=spec, root=sandbox, ask=lambda *a: True)
        assert not decision.allowed, mode
        assert decision.tier == "admin"


def test_messages_say_what_the_model_should_do_next(sandbox: Path, specs):
    hard = run("bash", {"command": HARD}, spec=specs["manual"], root=sandbox)
    beyond = run("bash", {"command": OUTSIDE_WRITE}, spec=specs["manual"], root=sandbox)
    danger = run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox)

    assert "永久禁止" in hard.message
    assert "不要重复尝试同一目标" in beyond.message
    assert "不要重复提交同一条命令" in danger.message
    assert len({hard.message, beyond.message, danger.message}) == 3


def test_outside_reads_never_reach_approval(sandbox: Path, specs):
    """读工作区之外是沙箱已有能力，所以任何一种模式下都不该打问号。"""
    ledger = ApprovalLedger()

    def ask(*args):
        raise AssertionError("区外读不该触发审批")

    for tool, arguments in (
        ("bash", {"command": OUTSIDE}),
        ("read_file", {"path": "/etc/hostname"}),
    ):
        decision = run(
            tool, arguments, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask
        )
        assert decision.allowed and decision.answered_by == "policy"
    assert ledger.path_grants() == ()



def test_approval_answers_grant_rw_for_write_commands(sandbox: Path, specs):
    with outside_files("rw-existing.txt") as (outside,):
        ledger = ApprovalLedger()
        decision = run(
            "bash",
            {"command": f"echo x > {outside}"},
            spec=specs["manual"],
            root=sandbox,
            ledger=ledger,
            ask=lambda *a: True,
        )
        assert decision.allowed
        assert decision.grants == ((str(outside), "rw"),)
        assert specs["manual"].approval == APPROVAL_USER
