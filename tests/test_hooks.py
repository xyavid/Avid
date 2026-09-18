import pytest

from avid.runtime import hooks
from avid.runtime.hooks import ALLOW, BLOCK, register_hook, trigger_hooks


@pytest.fixture
def clean(monkeypatch):
    """隔离全局注册表：每个用例从空表开始，自己注册需要的回调。"""
    monkeypatch.setattr(hooks, "HOOKS", {event: [] for event in hooks.EVENTS})
    return hooks.HOOKS


# ---------- 注册表与调用约定 ----------


def test_no_hooks_means_allow(clean):
    assert trigger_hooks("PreToolUse", {}) == ALLOW


def test_hooks_run_in_registration_order(clean):
    order = []
    register_hook("PreToolUse", lambda ctx: order.append("a"))
    register_hook("PreToolUse", lambda ctx: order.append("b"))

    trigger_hooks("PreToolUse", {})

    assert order == ["a", "b"]


def test_all_hooks_run_even_after_one_blocks(clean):
    calls = []

    def blocker(ctx):
        calls.append("blocker")
        return BLOCK

    register_hook("PreToolUse", blocker)
    register_hook("PreToolUse", lambda ctx: calls.append("logger"))

    assert trigger_hooks("PreToolUse", {}) == BLOCK
    assert calls == ["blocker", "logger"]


def test_any_block_blocks_the_event(clean):
    register_hook("PreToolUse", lambda ctx: None)
    register_hook("PreToolUse", lambda ctx: BLOCK)

    assert trigger_hooks("PreToolUse", {}) == BLOCK


def test_hooks_share_one_context_dict(clean):
    def writer(ctx):
        ctx["handoff"] = 42

    def reader(ctx):
        ctx["seen"] = ctx["handoff"]

    register_hook("PreToolUse", writer)
    register_hook("PreToolUse", reader)
    context = {}

    trigger_hooks("PreToolUse", context)

    assert context["seen"] == 42


def test_trigger_writes_the_event_name_into_context(clean):
    seen = {}
    register_hook("PostToolUse", lambda ctx: seen.update(ctx))

    trigger_hooks("PostToolUse", {"tool": "bash"})

    assert seen["event"] == "PostToolUse"


def test_hook_exception_blocks(clean):
    def boom(ctx):
        raise RuntimeError("坏了")

    register_hook("PreToolUse", boom)

    assert trigger_hooks("PreToolUse", {}) == BLOCK


def test_a_broken_hook_does_not_hide_the_others(clean):
    calls = []

    def boom(ctx):
        raise RuntimeError("坏了")

    register_hook("PreToolUse", boom)
    register_hook("PreToolUse", lambda ctx: calls.append("after"))

    trigger_hooks("PreToolUse", {})

    assert calls == ["after"]


def test_unknown_event_name_is_rejected(clean):
    with pytest.raises(ValueError, match="未知事件名"):
        register_hook("pretooluse", lambda ctx: None)

    with pytest.raises(ValueError, match="未知事件名"):
        trigger_hooks("PreToolUse ", {})


def test_register_hook_works_as_a_decorator(clean):
    @register_hook("Stop")
    def my_hook(ctx):
        return None

    assert hooks.HOOKS["Stop"] == [my_hook]


def test_default_hooks_are_registered_on_import():
    assert hooks.HOOKS["UserPromptSubmit"] == [hooks.context_inject_hook]
    assert hooks.HOOKS["PreToolUse"] == [hooks.permission_hook, hooks.log_hook]
    assert hooks.HOOKS["PostToolUse"] == [hooks.large_output_hook, hooks.log_hook]
    assert hooks.HOOKS["Stop"] == [hooks.summary_hook]


# ---------- 五个回调各自的行为 ----------


def test_context_inject_hook_reports_environment(clean):
    context = {"prompt": "你好", "injected": []}

    assert hooks.context_inject_hook(context) is None
    assert len(context["injected"]) == 1
    assert "工作区根目录" in context["injected"][0]
    assert "bash" in context["injected"][0]


def test_permission_hook_blocks_and_records_reason(clean):
    """严格模式下的常规拒绝：分类是 user，理由里带工具名与规则原文。"""
    context = {
        "tool": "bash",
        "arguments": {"command": "ls"},
        "permission_mode": "strict",
        "ask": lambda name, arguments, reason: False,
    }

    assert hooks.permission_hook(context) == BLOCK
    assert context["denied_kind"] == "user"
    assert context["denied_reason"] == "bash：执行 shell 命令"
    assert "本次未获用户批准" in context["denied_content"]


def test_permission_hook_allows_and_stays_quiet(clean):
    context = {"tool": "read_file", "arguments": {"path": "a"}}

    assert hooks.permission_hook(context) is None
    assert "denied_reason" not in context
    assert "denied_content" not in context


def test_permission_hook_reports_hard_deny_reason(clean):
    context = {"tool": "bash", "arguments": {"command": "rm -rf /"}}

    assert hooks.permission_hook(context) == BLOCK
    assert context["denied_kind"] == "hard"
    assert "删除根目录或家目录" in context["denied_reason"]
    assert "永久禁止" in context["denied_content"]


def test_hard_deny_and_user_refusal_give_different_guidance(clean):
    """两种拒绝必须让模型看到不同的话，否则它分不清"永远不许"和"这次不行"。"""
    hard = {"tool": "bash", "arguments": {"command": "rm -rf /"}}
    user = {
        "tool": "bash",
        "arguments": {"command": "ls"},
        "ask": lambda name, arguments, reason: False,
    }

    hooks.permission_hook(hard)
    hooks.permission_hook(user)

    assert hard["denied_kind"] == "hard"
    assert user["denied_kind"] == "user"
    assert hard["denied_content"] != user["denied_content"]


def test_brief_redacts_credentials_and_truncates():
    """工具参数进日志前必须脱敏：INFO 是默认级别，而命令里常带 token。"""
    from avid.runtime.hooks import brief

    line = brief({"command": 'curl -H "Authorization: Bearer sk-live-abc123" https://x'})
    assert "sk-live-abc123" not in line
    assert "***" in line

    nested = brief({"content": "API_KEY=topsecret", "path": "a.txt"})
    assert "topsecret" not in nested

    assert brief({"api_key": "plain-secret"}) == '{"api_key": "***"}'

    long_line = brief({"command": "x" * 1000})
    assert len(long_line) < 400 and long_line.endswith("（已截断）")


def test_permission_hook_routes_auto_approve_to_the_answerer(clean):
    """``--yes`` 只换回答者：注入的 ask 不被调用，硬拒绝仍被拦住。"""
    asked = []
    auto = {
        "tool": "bash",
        "arguments": {"command": "sudo apt-get install -y x"},
        "auto_approve": True,
        "ask": lambda *args: asked.append(args) or False,
    }

    assert hooks.permission_hook(auto) is None
    assert asked == []

    hard = {"tool": "bash", "arguments": {"command": "rm -rf /"}, "auto_approve": True}

    assert hooks.permission_hook(hard) == BLOCK
    assert hard["denied_kind"] == "hard"


def test_permission_hook_uses_the_injected_ask_without_the_run_flag(clean):
    """注入了 ask 就必须用它——Web 路径的审批不能落到 stdin 上（§7.2）。"""
    seen = []
    ask = lambda name, arguments, reason: seen.append((name, reason)) or True  # noqa: E731

    context = {"tool": "bash", "arguments": {"command": "ls"}, "ask": ask}

    assert hooks.permission_hook(context) is None
    assert seen == [("bash", "执行 shell 命令")]


def test_log_hook_never_blocks(clean):
    assert hooks.log_hook({"event": "PreToolUse", "tool": "bash", "arguments": {}}) is None
    assert hooks.log_hook({"event": "PostToolUse", "tool": "bash", "content": "x"}) is None


def test_large_output_hook_truncates(clean, monkeypatch):
    monkeypatch.setattr(hooks, "MAX_TOOL_OUTPUT_CHARS", 100)
    context = {"content": "x" * 1000, "truncated": False}

    assert hooks.large_output_hook(context) is None
    assert context["truncated"] is True
    assert context["content"].startswith("x")
    assert "原文 1000 字符" in context["content"]
    # 连截断提示一起算进预算，不超上限。
    assert len(context["content"]) <= 100


def test_large_output_hook_leaves_small_output_alone(clean):
    context = {"content": "short", "truncated": False}

    hooks.large_output_hook(context)

    assert context["content"] == "short"
    assert context["truncated"] is False


def test_summary_hook_writes_a_summary(clean):
    context = {"rounds": 3, "tool_calls": 5, "denials": 2}

    assert hooks.summary_hook(context) is None
    assert context["summary"] == "轮数=3 工具调用=5 拒绝=2"
