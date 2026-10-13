"""The hook surface: registry semantics, calling convention and the BLOCK contracts at the two tool
points.

What the body itself enforces is not here — those guards are plain code and are covered in
test_guards.py — and neither is the declared-script route (test_hook_scripts.py); this file pins how
callbacks are registered and what BLOCK means.
"""

import pytest

from avid.agent.hooks import (
    ALLOW,
    BLOCK,
    HookRegistry,
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


# ---- the registry belongs to the run ----


def test_two_runs_use_different_registries():
    """An injected registry affects this run only; the module-level dict it replaced would leak
    a callback into concurrent runs and subagents."""
    from support import run_loop

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
        state = RunState.for_run(hooks=registry, auto_approve=True, audit_enabled=False)
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
    from avid.agent.state import RunState

    def runner(arguments, *, state=None):
        return "SECRET=topsecret"

    registry = {"bash": runner}

    def blocking(context: dict) -> str:
        return BLOCK if "topsecret" in str(context.get("content")) else None

    registry_obj = HookRegistry()
    registry_obj.register("PostToolUse", blocking)
    state = RunState.for_run(hooks=registry_obj, auto_approve=True, audit_enabled=False)
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
    state = RunState.for_run(hooks=HookRegistry(), auto_approve=True, audit_enabled=False)

    content = execute_one(
        "bash", '{"command": "echo"}', registry, state=state, round_index=0
    )

    assert content == "正常输出"
