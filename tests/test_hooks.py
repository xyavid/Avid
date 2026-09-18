import pytest

from avid.runtime import hooks
from avid.runtime.hooks import ALLOW, BLOCK, DEFAULT_HOOKS, HookRegistry


@pytest.fixture
def clean() -> HookRegistry:
    """每个用例一份空注册表：自己注册需要的回调，不碰进程级那份。"""
    return HookRegistry()


# ---------- 注册表与调用约定 ----------


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
    assert DEFAULT_HOOKS.registered("UserPromptSubmit") == [hooks.context_inject_hook]
    assert DEFAULT_HOOKS.registered("PreToolUse") == [hooks.permission_hook, hooks.log_hook]
    assert DEFAULT_HOOKS.registered("PostToolUse") == [hooks.large_output_hook, hooks.log_hook]
    assert DEFAULT_HOOKS.registered("Stop") == [hooks.summary_hook]


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


# ---------- 注册表归运行所有（P2-19） ----------


def test_two_runs_use_different_registries():
    """注入的注册表只影响这一次运行。

    以前注册表是模块级字典：任何一处 `register_hook` 都会漏到同一进程里所有运行
    （含子 agent），测试也只能 monkeypatch 全局字典来隔离。现在"这次运行用哪份"
    是一个能看见、能替换的值——这条用例在旧设计下根本写不出来。
    """
    from avid.ai.client import Turn, Usage
    from avid.ai.config import Config
    from avid.runtime.hooks import HookRegistry
    from avid.runtime.loop import agent_loop
    from avid.runtime.state import RunState

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
        agent_loop(
            [{"role": "user", "content": "hi"}],
            config=config,
            chat=Chat(),
            state=state,
        )

    run(HookRegistry())  # 安静的那次：什么都没注册
    assert seen == []
    run(loud)
    assert seen == ["bash"], "注册在 loud 上的回调只该在 loud 那次运行里触发"


def test_copy_is_independent_but_inherits():
    """子运行拿的是父注册表的副本：继承已有回调，自己追加的不回漏。"""
    from avid.runtime.hooks import HookRegistry

    parent = HookRegistry()
    parent.register("Stop", lambda context: None)
    clone = parent.copy()
    child_only: list[int] = []
    clone.register("Stop", lambda context: child_only.append(1))

    assert len(parent.registered("Stop")) == 1, "父注册表不该被子运行改动"
    assert len(clone.registered("Stop")) == 2, "副本继承父的回调"


# ---------- PostToolUse 的 BLOCK 语义（P3-6） ----------


def test_post_tool_use_block_stops_the_result_from_entering_context():
    """PostToolUse 返回 BLOCK 时必须真的拦住结果。

    以前这个返回值被直接丢掉：注册了拦截的回调等于静默失效，而且失败方向正好是
    最糟的那个——内容照样进了上下文（比如输出里带凭据）。
    """
    from avid.runtime.execution import POST_BLOCKED_CONTENT, execute_one

    def runner(arguments, *, state=None):
        return "SECRET=topsecret"

    registry = {"bash": runner}

    def blocking(context: dict) -> str:
        return BLOCK if "topsecret" in str(context.get("content")) else None

    registry_obj = HookRegistry()
    registry_obj.register("PostToolUse", blocking)
    from avid.runtime.state import RunState

    state = RunState.for_run(hooks=registry_obj, auto_approve=True)
    content = execute_one(
        "bash", '{"command": "env"}', registry, state=state, round_index=0
    )

    assert content == POST_BLOCKED_CONTENT
    assert "topsecret" not in content, "被拦的内容不能回给模型"


def test_post_tool_use_without_block_passes_the_content_through():
    """没有拦截时结果照常回传（别把"显式处理 BLOCK"做成"总是拦截"）。"""
    from avid.runtime.execution import execute_one
    from avid.runtime.state import RunState

    registry = {"bash": lambda arguments, *, state=None: "正常输出"}
    state = RunState.for_run(hooks=HookRegistry(), auto_approve=True)

    content = execute_one(
        "bash", '{"command": "echo"}', registry, state=state, round_index=0
    )

    assert content == "正常输出"
