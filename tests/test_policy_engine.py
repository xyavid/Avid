"""Policy Engine：三模式 × 动作类别的决策表、能力账本、分类器、失败关闭。

这里的每一行都是产品规格的一句话：谁回答 REVIEW（人 / 分类器 / 无人）、
沙箱能保证的事不打搅人、降级不静默、deny 连 full 也不放行。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from avid.policy.permission import (
    APPROVAL_CLASSIFIER,
    APPROVAL_NONE,
    APPROVAL_USER,
    ApprovalLedger,
    BackendProbe,
    brokerize,
    build_run_security,
    decide,
)
from avid.policy.sandbox import BACKEND_BWRAP, BACKEND_NONE

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
HARD = "rm -rf /"


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
    """越界按**路径**记账：同意 `/etc/hostname` 不等于同意 `/etc/hosts`。"""
    ledger = ApprovalLedger()
    asked: list[str] = []

    def ask(name, arguments, reason):
        asked.append(reason)
        return True

    first = run(
        "bash", {"command": OUTSIDE}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask
    )
    assert first.allowed and first.grants == (("/etc/hostname", "ro"),)
    assert len(asked) == 1

    # 同一个目标再来一次：账本命中，不再问
    again = run(
        "bash", {"command": OUTSIDE}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask
    )
    assert again.allowed and again.answered_by == "ledger"
    assert len(asked) == 1

    # 换一个区外目标：重新问
    other = run(
        "bash", {"command": "cat /etc/hosts"}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=ask
    )
    assert other.allowed and len(asked) == 2


def test_auto_never_asks_the_user(sandbox: Path, specs):
    def ask(*args):
        raise AssertionError("auto 下没有人在环，不该调用审批回调")

    decision = run("bash", {"command": SECRET}, spec=specs["auto"], root=sandbox, ask=ask)
    assert not decision.allowed
    assert (decision.kind, decision.answered_by) == ("danger", "classifier")
    assert "自动审查判定风险过高" in decision.message
    assert specs["auto"].approval == APPROVAL_CLASSIFIER


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
    ("越界", "bash", {"command": OUTSIDE}, ("deny", "outside"), ("deny", "outside"), ("allow", "outside")),
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
    ("越界", "bash", {"command": OUTSIDE}, "outside"),
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
def test_auto_answers_the_same_review_by_itself(sandbox: Path, specs, label, tool, arguments, kind):
    """auto 把同一批 REVIEW 交给分类器：放行或拒绝，**绝不问人**。"""
    decision = run(tool, arguments, spec=specs["auto"], root=sandbox, ledger=ApprovalLedger())
    assert decision.answered_by in {"classifier", "policy"}
    if not decision.allowed:
        assert decision.kind == kind
        assert "自动审查判定风险过高" in decision.message


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
    auto = security(sandbox, "auto", probe=BROKEN_PROBE)

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

    denied = run("bash", {"command": "ls"}, spec=auto, root=sandbox)
    assert (denied.verdict, denied.kind) == ("deny", "degraded")

    # 区内只读不因为"没有沙箱"而多问一句（它的保证来自路径校验，不来自沙箱）
    assert run("read_file", {"path": "a.txt"}, spec=manual, root=sandbox).allowed


def test_degraded_network_still_fails_closed(sandbox: Path):
    spec = security(sandbox, "manual", probe=BROKEN_PROBE)
    decision = run("bash", {"command": "curl https://api.example.com"}, spec=spec, root=sandbox, ask=lambda *a: True)
    assert (decision.type, decision.code) == ("SANDBOX_DENIED", "SANDBOX_NETWORK_DENIED")


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


def test_file_tool_outside_read_grant_never_allows_write(sandbox: Path, specs):
    from avid.runtime.state import RunState
    from avid.tools.files import write_file

    outside = sandbox.parent / "outside-access.txt"
    outside.write_text("original")
    state = RunState.for_run(security=specs["manual"], workspace_root=str(sandbox))
    state.ledger.remember(("path", str(outside), "ro"))
    assert "拒绝访问" in write_file({"path": str(outside), "content": "x"}, state=state)
    assert outside.read_text() == "original"


    ledger = ApprovalLedger()
    ledger.remember(("path", "/etc/hostname", "ro"))
    multiple = run("bash", {"command": "cat /etc/hostname /etc/hosts"}, spec=specs["manual"], root=sandbox, ledger=ledger)
    assert multiple.type == "NEEDS_APPROVAL"
    write = run("bash", {"command": "echo x > /etc/hostname"}, spec=specs["manual"], root=sandbox, ledger=ledger)
    assert write.type == "NEEDS_APPROVAL"


    ledger = ApprovalLedger()
    run("bash", {"command": OUTSIDE}, spec=specs["manual"], root=sandbox, ledger=ledger, ask=lambda *a: True)
    assert ledger.path_grants() == (("/etc/hostname", "ro"),)
    assert ledger.outside_allowed("/etc/hostname") is True
    assert ledger.outside_allowed("/etc/shadow") is False


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
    outside = run("bash", {"command": OUTSIDE}, spec=specs["manual"], root=sandbox)
    danger = run("bash", {"command": SECRET}, spec=specs["manual"], root=sandbox)

    assert "永久禁止" in hard.message
    assert "不要重复尝试同一路径" in outside.message
    assert "不要重复提交同一条命令" in danger.message
    assert len({hard.message, outside.message, danger.message}) == 3


def test_approval_answers_grant_read_only_for_reads(sandbox: Path, specs):
    ledger = ApprovalLedger()
    # read_file 的越界目标是只读授予：写不进去，因为沙箱按 ro 挂
    decision = run(
        "read_file",
        {"path": "/etc/hostname"},
        spec=specs["manual"],
        root=sandbox,
        ledger=ledger,
        ask=lambda *a: True,
    )
    assert decision.allowed
    assert decision.grants == (("/etc/hostname", "ro"),)


def test_approval_answers_grant_rw_for_write_commands(sandbox: Path, specs):
    outside = sandbox.parent / "rw-existing.txt"
    outside.write_text("original")
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
