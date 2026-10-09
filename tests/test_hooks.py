import pytest

from avid.agent.hooks import (
    ALLOW,
    BLOCK,
    DEFAULT_HOOKS,
    HookRegistry,
    large_output_hook,
    log_hook,
    permission_hook,
    repeat_call_hook,
    summary_hook,
)


@pytest.fixture
def clean() -> HookRegistry:
    """A fresh empty registry per test: register your own callbacks, never the shared one."""
    return HookRegistry()


# ---- registry and calling convention ----


def test_no_hooks_means_allow(clean):
    assert clean.trigger("PreToolUse", {}) == ALLOW


def test_hooks_run_in_registration_order(clean):
    order = []
    clean.register("PreToolUse", lambda ctx: order.append("a"))
    clean.register("PreToolUse", lambda ctx: order.append("b"))

    clean.trigger("PreToolUse", {})

    assert order == ["a", "b"]


def test_all_hooks_run_even_after_one_blocks(clean):
    calls = []

    def blocker(ctx):
        calls.append("blocker")
        return BLOCK

    clean.register("PreToolUse", blocker)
    clean.register("PreToolUse", lambda ctx: calls.append("logger"))

    assert clean.trigger("PreToolUse", {}) == BLOCK
    assert calls == ["blocker", "logger"]


def test_any_block_blocks_the_event(clean):
    clean.register("PreToolUse", lambda ctx: None)
    clean.register("PreToolUse", lambda ctx: BLOCK)

    assert clean.trigger("PreToolUse", {}) == BLOCK


def test_hooks_share_one_context_dict(clean):
    def writer(ctx):
        ctx["handoff"] = 42

    def reader(ctx):
        ctx["seen"] = ctx["handoff"]

    clean.register("PreToolUse", writer)
    clean.register("PreToolUse", reader)
    context = {}

    clean.trigger("PreToolUse", context)

    assert context["seen"] == 42


def test_trigger_writes_the_event_name_into_context(clean):
    seen = {}
    clean.register("PostToolUse", lambda ctx: seen.update(ctx))

    clean.trigger("PostToolUse", {"tool": "bash"})

    assert seen["event"] == "PostToolUse"


def test_hook_exception_blocks(clean):
    def boom(ctx):
        raise RuntimeError("坏了")

    clean.register("PreToolUse", boom)

    assert clean.trigger("PreToolUse", {}) == BLOCK


def test_a_broken_hook_does_not_hide_the_others(clean):
    calls = []

    def boom(ctx):
        raise RuntimeError("坏了")

    clean.register("PreToolUse", boom)
    clean.register("PreToolUse", lambda ctx: calls.append("after"))

    clean.trigger("PreToolUse", {})

    assert calls == ["after"]


def test_unknown_event_name_is_rejected(clean):
    with pytest.raises(ValueError, match="未知事件名"):
        clean.register("pretooluse", lambda ctx: None)

    with pytest.raises(ValueError, match="未知事件名"):
        clean.trigger("PreToolUse ", {})


def test_register_hook_works_as_a_decorator(clean):
    @clean.register("Stop")
    def my_hook(ctx):
        return None

    assert clean.registered("Stop") == [my_hook]


def test_default_hooks_are_registered_on_import():
    # No default callback on UserPromptSubmit: the environment block owns that
    assert DEFAULT_HOOKS.registered("UserPromptSubmit") == []
    assert DEFAULT_HOOKS.registered("PreToolUse") == [permission_hook, log_hook]
    assert DEFAULT_HOOKS.registered("PostToolUse") == [
        repeat_call_hook,
        large_output_hook,
        log_hook,
    ]
    assert DEFAULT_HOOKS.registered("Stop") == [summary_hook]


# ---- individual callback behaviour ----


def test_permission_hook_does_not_question_dangerous_categories(clean):
    """Dangerous categories (sudo etc.) skip the ask; the risk name goes to the audit only."""
    asked = []
    context = {
        "tool": "bash",
        "arguments": {"command": "sudo ls"},
        "ask": lambda name, arguments, reason: asked.append((name, reason)) or False,
    }

    assert permission_hook(context) is None
    assert asked == [], "危险类别不经过询问通道，ask 不该被调用"
    assert "denied_reason" not in context


def test_permission_hook_lets_the_sandbox_cover_ordinary_commands(sandbox, clean):
    """An ordinary in-workspace command needs no approval: the sandbox already guarantees it."""
    from avid.agent.state import RunState

    state = RunState.for_run(workspace_root=str(sandbox), audit_enabled=False)

    def ask(*args):
        raise AssertionError("沙箱能保证的动作不该问人")

    context = {
        "tool": "bash",
        "arguments": {"command": "ls"},
        "security": state.security,
        "approval_ledger": state.ledger,
        "workspace_root": str(sandbox),
        "ask": ask,
    }

    assert permission_hook(context) is None
    assert "denied_reason" not in context


def test_permission_hook_writes_the_audit_record(sandbox, clean, tmp_path, monkeypatch):
    """Even an allow is recorded: verdict, answerer and the outside-write fact go to the audit."""
    import json

    from avid.agent.state import RunState

    monkeypatch.setenv("AVID_AUDIT_DIR", str(tmp_path / "audit"))
    state = RunState.for_run(workspace_root=str(sandbox))
    context = {
        "tool": "bash",
        "arguments": {"command": "echo x >> /etc/hostname"},
        "security": state.security,
        "approval_ledger": state.ledger,
        "workspace_root": str(sandbox),
    }

    assert permission_hook(context) is None  # out-of-area writes run without asking

    records = [
        json.loads(line)
        for line in (tmp_path / "audit").glob("audit-*.jsonl").__iter__().__next__().read_text(
            encoding="utf-8"
        ).splitlines()
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


def test_permission_hook_allows_and_stays_quiet(clean):
    context = {"tool": "read_file", "arguments": {"path": "a"}}

    assert permission_hook(context) is None
    assert "denied_reason" not in context
    assert "denied_content" not in context


def test_permission_hook_refuses_destruction_without_an_ask_channel(clean):
    context = {"tool": "bash", "arguments": {"command": "rm -rf /"}}

    assert permission_hook(context) == BLOCK
    assert context["denied_kind"] == "danger"
    assert "删除根目录或家目录" in context["denied_reason"]
    assert "没有可用的询问通道" in context["denied_content"]


def test_unanswered_and_refused_destruction_give_different_guidance(clean):
    """The two refusals read differently: nobody to ask vs the user refused (do not resubmit)."""
    unanswered = {"tool": "bash", "arguments": {"command": "rm -rf /"}}
    refused = {
        "tool": "bash",
        "arguments": {"command": "rm -rf /"},
        "ask": lambda name, arguments, reason: False,
    }

    permission_hook(unanswered)
    permission_hook(refused)

    assert unanswered["denied_kind"] == "danger"
    assert refused["denied_kind"] == "danger"
    assert unanswered["denied_content"] != refused["denied_content"]
    assert "没有可用的询问通道" in unanswered["denied_content"]
    assert "不要重复提交同一条命令" in refused["denied_content"]


def test_brief_redacts_credentials_and_truncates():
    """Tool arguments are redacted before logging: INFO is the default level and commands
    often carry tokens."""
    from avid.agent.hooks import brief

    line = brief({"command": 'curl -H "Authorization: Bearer sk-live-abc123" https://x'})
    assert "sk-live-abc123" not in line
    assert "***" in line

    nested = brief({"content": "API_KEY=topsecret", "path": "a.txt"})
    assert "topsecret" not in nested

    assert brief({"api_key": "plain-secret"}) == '{"api_key": "***"}'

    long_line = brief({"command": "x" * 1000})
    assert len(long_line) < 400 and long_line.endswith("（已截断）")


def test_permission_hook_routes_auto_approve_to_the_answerer(clean):
    """--yes swaps the answerer only: the injected ask stays uncalled, the credential
    hard deny still blocks."""
    asked = []
    auto = {
        "tool": "bash",
        "arguments": {"command": "sudo apt-get install -y x"},
        "auto_approve": True,
        "ask": lambda *args: asked.append(args) or False,
    }

    assert permission_hook(auto) is None  # dangerous categories go through no ask
    assert asked == []

    # Destructive: normally asked, auto_approve answers always_allow in the user's place
    destruction = {
        "tool": "bash",
        "arguments": {"command": "rm -rf /"},
        "auto_approve": True,
        "ask": lambda *args: asked.append(args) or False,
    }

    assert permission_hook(destruction) is None
    assert asked == []

    # Credential reads are the only hard deny; auto_approve does not override it
    credential = {
        "tool": "read_file",
        "arguments": {"path": "~/.ssh/id_rsa"},
        "auto_approve": True,
    }

    assert permission_hook(credential) == BLOCK
    assert credential["denied_kind"] == "credential"


def test_permission_hook_uses_the_injected_ask_without_the_run_flag(clean):
    """An injected ask must be used: the Web path's approval must never fall through to stdin."""
    seen = []
    ask = lambda name, arguments, reason: seen.append((name, reason)) or True  # noqa: E731

    context = {"tool": "bash", "arguments": {"command": "rm -rf /"}, "ask": ask}

    assert permission_hook(context) is None
    assert seen == [("bash", "删除根目录或家目录")]


def test_log_hook_never_blocks(clean):
    assert log_hook({"event": "PreToolUse", "tool": "bash", "arguments": {}}) is None
    assert log_hook({"event": "PostToolUse", "tool": "bash", "content": "x"}) is None


def test_large_output_hook_spills_the_full_text(clean, monkeypatch, sandbox):
    """Over the cap the full text spills to disk: the model sees head and tail and can read
    the rest back."""
    monkeypatch.setattr("avid.agent.hooks.MAX_TOOL_OUTPUT_CHARS", 400)
    payload = "头" * 300 + "尾" * 300
    context = {
        "content": payload,
        "truncated": False,
        "workspace_root": str(sandbox),
        "run_tag": "t1",
    }

    assert large_output_hook(context) is None

    content = context["content"]
    assert context["truncated"] is True
    assert len(content) <= 400
    assert "原文 600 字符" in content
    assert content.startswith("头")
    assert content.endswith("尾")

    from avid.agent.compaction import SPILL_DIR

    files = list((sandbox / SPILL_DIR).glob("tool-output-*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == payload
    assert files[0].name in content  # the hint carries the path back


def test_large_output_hook_falls_back_when_spill_fails(clean, monkeypatch, sandbox):
    """A failed spill must not lose the result or fail the call: fall back to the head only."""
    monkeypatch.setattr("avid.agent.hooks.MAX_TOOL_OUTPUT_CHARS", 100)
    blocked = sandbox / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    context = {
        "content": "x" * 1000,
        "truncated": False,
        "workspace_root": str(blocked),
    }

    assert large_output_hook(context) is None

    assert context["truncated"] is True
    assert context["content"].startswith("x")
    assert "原文 1000 字符" in context["content"]
    assert "已存至" not in context["content"]  # no fake recovery path when the spill failed
    # The truncation hint counts against the cap too
    assert len(context["content"]) <= 100


def test_large_output_hook_leaves_small_output_alone(clean):
    context = {"content": "short", "truncated": False}

    large_output_hook(context)

    assert context["content"] == "short"
    assert context["truncated"] is False


def test_repeat_call_hook_reminds_on_the_third_and_fifth_time(clean):
    """A same-name, same-argument repeat is reminded on the 3rd and 5th time; others pass."""
    counts: dict[str, int] = {}
    for times in range(1, 6):
        context = {
            "tool": "bash",
            "arguments": {"command": "pytest -q"},
            "content": "输出",
            "repeat_calls": counts,
        }

        assert repeat_call_hook(context) is None

        if times in (3, 5):
            assert "[重复调用提醒]" in context["content"]
            assert f"重复 {times} 次" in context["content"]
            assert context["content"].startswith("输出"), "提醒是追加，不是替换"
        else:
            assert context["content"] == "输出"


def test_repeat_call_hook_treats_different_arguments_as_different_calls(clean):
    counts: dict[str, int] = {}

    def call(arguments):
        context = {
            "tool": "bash",
            "arguments": arguments,
            "content": "输出",
            "repeat_calls": counts,
        }
        repeat_call_hook(context)
        return context["content"]

    call({"command": "a"})
    call({"command": "b"})
    call({"command": "a"})
    reminded = call({"command": "a"})

    assert "[重复调用提醒]" in reminded
    assert sorted(counts.values()) == [1, 3]


def test_repeat_call_hook_ignores_key_order(clean):
    """Arguments compare as canonical JSON: a different key order is the same call."""
    counts: dict[str, int] = {}
    for arguments in ({"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": 1, "b": 2}):
        context = {
            "tool": "bash",
            "arguments": arguments,
            "content": "输出",
            "repeat_calls": counts,
        }
        repeat_call_hook(context)

    assert list(counts.values()) == [3]


def test_repeat_call_hook_is_inert_without_the_run_state(clean):
    """Without a RunState (direct calls, other callers) it neither errors nor guesses."""
    context = {"tool": "bash", "arguments": {"command": "a"}, "content": "输出"}

    assert repeat_call_hook(context) is None
    assert context["content"] == "输出"


def test_repeat_call_hook_counts_through_execute_one():
    """Wiring: execution must put RunState.repeat_calls into the PostToolUse context."""
    from avid.agent.execution import execute_one
    from avid.agent.state import RunState

    registry_obj = HookRegistry()
    registry_obj.register("PostToolUse", repeat_call_hook)
    state = RunState.for_run(hooks=registry_obj, auto_approve=True)
    registry = {"bash": lambda arguments, *, state=None: "输出"}

    contents = [
        execute_one("bash", '{"command": "same"}', registry, state=state, round_index=0)
        for _ in range(3)
    ]

    assert "[重复调用提醒]" not in contents[0]
    assert "[重复调用提醒]" not in contents[1]
    assert "重复 3 次" in contents[2]
    assert state.repeat_calls, "计数必须落在 RunState 上"


def test_summary_hook_writes_a_summary(clean):
    context = {"rounds": 3, "tool_calls": 5, "denials": 2}

    assert summary_hook(context) is None
    assert context["summary"] == "轮数=3 工具调用=5 拒绝=2"


# ---- the registry belongs to the run ----


def test_two_runs_use_different_registries():
    """An injected registry affects this run only; the module-level dict it replaced would leak
    a callback into concurrent runs and subagents."""
    from support import run_loop

    from avid.agent.hooks import HookRegistry
    from avid.agent.state import RunState
    from avid.providers.client import Turn, Usage
    from avid.providers.config import Config

    seen: list[str] = []
    loud = HookRegistry()
    loud.register("PreToolUse", lambda context: seen.append(context["tool"]))

    def call(call_id: str) -> dict:
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": "bash", "arguments": '{"command": "true"}'},
        }

    class Chat:
        def __init__(self):
            self.n = 0

        def __call__(self, config, messages, **kwargs):
            self.n += 1
            message = {"role": "assistant", "content": ""}
            if self.n == 1:
                message["tool_calls"] = [call("c1")]
            return Turn(
                message=message,
                text="",
                tool_calls=[call("c1")] if self.n == 1 else [],
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                model="m",
                finish_reason="stop",
            )

    config = Config(api_key="k", base_url="https://api.test/v1", model="m")

    def run(registry):
        state = RunState.for_run(hooks=registry, auto_approve=True)
        run_loop(
            [{"role": "user", "content": "hi"}],
            config=config,
            chat=Chat(),
            state=state,
        )

    run(HookRegistry())  # the quiet run: nothing registered
    assert seen == []
    run(loud)
    assert seen == ["bash"], "注册在 loud 上的回调只该在 loud 那次运行里触发"


def test_copy_is_independent_but_inherits():
    """A child run gets a copy: it inherits the parent's callbacks, additions never leak back."""
    from avid.agent.hooks import HookRegistry

    parent = HookRegistry()
    parent.register("Stop", lambda context: None)
    clone = parent.copy()
    child_only: list[int] = []
    clone.register("Stop", lambda context: child_only.append(1))

    assert len(parent.registered("Stop")) == 1, "父注册表不该被子运行改动"
    assert len(clone.registered("Stop")) == 2, "副本继承父的回调"


# ---- PostToolUse BLOCK semantics ----


def test_post_tool_use_block_stops_the_result_from_entering_context():
    """A PostToolUse BLOCK must really stop the result, or the hook is silently disabled and
    content (say, credentials) enters the context."""
    from avid.agent.execution import POST_BLOCKED_CONTENT, execute_one

    def runner(arguments, *, state=None):
        return "SECRET=topsecret"

    registry = {"bash": runner}

    def blocking(context: dict) -> str:
        return BLOCK if "topsecret" in str(context.get("content")) else None

    registry_obj = HookRegistry()
    registry_obj.register("PostToolUse", blocking)
    from avid.agent.state import RunState

    state = RunState.for_run(hooks=registry_obj, auto_approve=True)
    content = execute_one(
        "bash", '{"command": "env"}', registry, state=state, round_index=0
    )

    assert content == POST_BLOCKED_CONTENT
    assert "topsecret" not in content, "被拦的内容不能回给模型"


def test_post_tool_use_without_block_passes_the_content_through():
    """Without a block the content passes through; handling BLOCK is not always-blocking."""
    from avid.agent.execution import execute_one
    from avid.agent.state import RunState

    registry = {"bash": lambda arguments, *, state=None: "正常输出"}
    state = RunState.for_run(hooks=HookRegistry(), auto_approve=True)

    content = execute_one(
        "bash", '{"command": "echo"}', registry, state=state, round_index=0
    )

    assert content == "正常输出"
