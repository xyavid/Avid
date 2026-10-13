"""The loop's own enforcement: permission verdicts, the repeat reminder, the tool-output budget and
the run summary.

Plain functions, not hooks: these run on every call whatever the run's registry holds, which is the
point of the split — a replaceable registry must not be able to switch the body's gates off.
"""

import pytest

from avid.agent import guards
from avid.agent.guards import brief
from avid.agent.state import RunState


def finite(**kwargs) -> RunState:
    """A run state with the audit off, so a verdict test writes nothing anywhere."""
    kwargs.setdefault("audit_enabled", False)
    return RunState.for_run(**kwargs)


# ---- permission gate ----


def test_gate_does_not_question_dangerous_categories():
    """Dangerous categories (sudo etc.) skip the ask; the risk name goes to the audit only."""
    asked = []
    state = finite(ask=lambda name, arguments, reason: asked.append((name, reason)) or False)

    denial = guards.check_permission(state, tool="bash", arguments={"command": "sudo ls"})

    assert denial is None
    assert asked == [], "危险类别不经过询问通道，ask 不该被调用"


def test_gate_lets_the_sandbox_cover_ordinary_commands(sandbox):
    """An ordinary in-workspace command needs no approval: the sandbox already guarantees it."""
    state = finite(workspace_root=str(sandbox))

    def ask(*args):
        raise AssertionError("沙箱能保证的动作不该问人")

    state.ask = ask

    assert guards.check_permission(state, tool="bash", arguments={"command": "ls"}) is None


def test_gate_writes_the_audit_record(sandbox, tmp_path, monkeypatch):
    """Even an allow is recorded: verdict, answerer and the outside-write fact go to the audit."""
    import json

    monkeypatch.setenv("AVID_AUDIT_DIR", str(tmp_path / "audit"))
    state = RunState.for_run(workspace_root=str(sandbox))

    denial = guards.check_permission(
        state, tool="bash", arguments={"command": "echo x >> /etc/hostname"}
    )

    assert denial is None  # out-of-area writes run without asking

    records = [
        json.loads(line)
        for line in (tmp_path / "audit")
        .glob("audit-*.jsonl")
        .__iter__()
        .__next__()
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    record = records[-1]
    assert record["kind"] == "decision"
    assert record["tool"] == "bash"
    assert record["verdict"] == "allow" and record["decision_type"] == "SAFE_AUTO"
    assert record["answered_by"] == "policy"
    assert record["mode"] == "normal" and record["axes"] == {"full": False}
    assert "decision_kind" not in record and "danger" not in record
    assert "/etc/hostname" in record["outside"]
    assert "越界" in record["risks"]
    # The outside write is auto-granted into the ledger the sandbox mounts from
    assert state.ledger.path_grants() == (("/etc/hostname", "rw"),)


def test_gate_allows_and_stays_quiet():
    state = finite()

    assert guards.check_permission(state, tool="read_file", arguments={"path": "a"}) is None


def test_gate_refuses_destruction_without_an_ask_channel():
    denial = guards.check_permission(
        finite(), tool="bash", arguments={"command": "rm -rf /"}
    )

    assert denial is not None
    assert denial.kind == "danger"
    assert "删除根目录或家目录" in denial.reason
    assert "没有可用的询问通道" in denial.content


def test_unanswered_and_refused_destruction_give_different_guidance():
    """The two refusals read differently: nobody to ask vs the user refused (do not resubmit)."""
    unanswered = guards.check_permission(
        finite(), tool="bash", arguments={"command": "rm -rf /"}
    )
    refused = guards.check_permission(
        finite(ask=lambda name, arguments, reason: False),
        tool="bash",
        arguments={"command": "rm -rf /"},
    )

    assert unanswered is not None and refused is not None
    assert unanswered.kind == "danger" and refused.kind == "danger"
    assert unanswered.content != refused.content
    assert "没有可用的询问通道" in unanswered.content
    assert "不要重复提交同一条命令" in refused.content


def test_gate_routes_auto_approve_to_the_answerer():
    """--yes swaps the answerer only: the injected ask stays uncalled, the credential
    hard deny still blocks."""
    asked = []
    ask = lambda *args: asked.append(args) or False  # noqa: E731
    state = finite(auto_approve=True, ask=ask)

    # Dangerous categories go through no ask
    assert (
        guards.check_permission(
            state, tool="bash", arguments={"command": "sudo apt-get install -y x"}
        )
        is None
    )
    # Destructive: normally asked, auto_approve answers always_allow in the user's place
    assert (
        guards.check_permission(state, tool="bash", arguments={"command": "rm -rf /"}) is None
    )
    # Credential reads are the only hard deny; auto_approve does not override it
    denial = guards.check_permission(
        state, tool="read_file", arguments={"path": "~/.ssh/id_rsa"}
    )

    assert asked == []
    assert denial is not None and denial.kind == "credential"


def test_gate_uses_the_injected_ask_without_the_run_flag():
    """An injected ask must be used: the Web path's approval must never fall through to stdin."""
    seen = []
    ask = lambda name, arguments, reason: seen.append((name, reason)) or True  # noqa: E731

    denial = guards.check_permission(
        finite(ask=ask), tool="bash", arguments={"command": "rm -rf /"}
    )

    assert denial is None
    assert seen == [("bash", "删除根目录或家目录")]


def test_brief_redacts_credentials_and_truncates():
    """Tool arguments are redacted before logging: INFO is the default level and commands
    often carry tokens."""
    line = brief({"command": 'curl -H "Authorization: Bearer sk-live-abc123" https://x'})
    assert "sk-live-abc123" not in line
    assert "***" in line

    nested = brief({"content": "API_KEY=topsecret", "path": "a.txt"})
    assert "topsecret" not in nested

    assert brief({"api_key": "plain-secret"}) == '{"api_key": "***"}'

    long_line = brief({"command": "x" * 1000})
    assert len(long_line) < 400 and long_line.endswith("（已截断）")


# ---- tool-output budget ----


def test_fit_tool_output_spills_the_full_text(monkeypatch, sandbox):
    """Over the cap the full text spills to disk: the model sees head and tail and can read
    the rest back."""
    monkeypatch.setattr(guards, "MAX_TOOL_OUTPUT_CHARS", 400)
    payload = "头" * 300 + "尾" * 300
    state = RunState(workspace_root=str(sandbox), run_tag="t1")

    content, truncated = guards.fit_tool_output(state, content=payload)

    assert truncated is True
    assert len(content) <= 400
    assert "原文 600 字符" in content
    assert content.startswith("头")
    assert content.endswith("尾")

    from avid.agent.compaction import SPILL_DIR

    files = list((sandbox / SPILL_DIR).glob("tool-output-*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == payload
    assert files[0].name in content  # the hint carries the path back


def test_fit_tool_output_falls_back_when_spill_fails(monkeypatch, sandbox):
    """A failed spill must not lose the result or fail the call: fall back to the head only."""
    monkeypatch.setattr(guards, "MAX_TOOL_OUTPUT_CHARS", 100)
    blocked = sandbox / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    state = RunState(workspace_root=str(blocked))

    content, truncated = guards.fit_tool_output(state, content="x" * 1000)

    assert truncated is True
    assert content.startswith("x")
    assert "原文 1000 字符" in content
    assert "已存至" not in content  # no fake recovery path when the spill failed
    # The truncation hint counts against the cap too
    assert len(content) <= 100


def test_fit_tool_output_leaves_small_output_alone():
    content, truncated = guards.fit_tool_output(RunState(), content="short")

    assert content == "short" and truncated is False


# ---- repeat reminder ----


def test_note_repeat_reminds_on_the_third_and_fifth_time():
    """A same-name, same-argument repeat is reminded on the 3rd and 5th time; others pass."""
    state = RunState()
    for times in range(1, 6):
        content = guards.note_repeat(
            state, tool="bash", arguments={"command": "pytest -q"}, content="输出"
        )

        if times in (3, 5):
            assert "[重复调用提醒]" in content
            assert f"重复 {times} 次" in content
            assert content.startswith("输出"), "提醒是追加，不是替换"
        else:
            assert content == "输出"


def test_note_repeat_treats_different_arguments_as_different_calls():
    state = RunState()

    def call(arguments):
        return guards.note_repeat(state, tool="bash", arguments=arguments, content="输出")

    call({"command": "a"})
    call({"command": "b"})
    call({"command": "a"})
    reminded = call({"command": "a"})

    assert "[重复调用提醒]" in reminded
    assert sorted(state.repeat_calls.values()) == [1, 3]


def test_note_repeat_ignores_key_order():
    """Arguments compare as canonical JSON: a different key order is the same call."""
    state = RunState()
    for arguments in ({"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": 1, "b": 2}):
        guards.note_repeat(state, tool="bash", arguments=arguments, content="输出")

    assert list(state.repeat_calls.values()) == [3]


def test_repeat_counting_runs_through_execute_one():
    """Wiring: the counter really lives on the run state and survives execute_one."""
    from avid.agent.execution import execute_one

    state = RunState.for_run(auto_approve=True, audit_enabled=False)
    registry = {"bash": lambda arguments, *, state=None: "输出"}

    contents = [
        execute_one("bash", '{"command": "same"}', registry, state=state, round_index=0)
        for _ in range(3)
    ]

    assert "[重复调用提醒]" not in contents[0]
    assert "[重复调用提醒]" not in contents[1]
    assert "重复 3 次" in contents[2]
    assert state.repeat_calls, "计数必须落在 RunState 上"


# ---- run summary ----


def test_summarize_run_writes_a_summary(caplog):
    state = RunState(round=3, tool_calls=5, denials=2)

    with caplog.at_level("INFO", logger="avid.agent.guards"):
        summary = guards.summarize_run(state)

    assert summary == "轮数=3 工具调用=5 拒绝=2"
    assert any("运行汇总" in record.getMessage() for record in caplog.records)


def test_the_body_gate_runs_without_any_registry():
    """The premise of the whole split: an empty registry still refuses a credential read."""
    from avid.agent.execution import execute_one
    from avid.agent.hooks import HookRegistry

    state = RunState.for_run(hooks=HookRegistry(), audit_enabled=False)
    executed = []

    content = execute_one(
        "read_file",
        '{"path": "~/.ssh/id_rsa"}',
        {"read_file": lambda arguments, **kwargs: executed.append(arguments) or "SECRET"},
        state=state,
        round_index=0,
    )

    assert executed == [], "闸门不在了，凭据读取被执行了"
    assert "SECRET" not in content


@pytest.mark.parametrize("event", ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"])
def test_no_default_callback_is_registered(event):
    from avid.agent.hooks import DEFAULT_HOOKS

    assert DEFAULT_HOOKS.registered(event) == []
