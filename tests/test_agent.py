import pytest

from avid.ai.client import Turn, Usage
from avid.ai.config import Config
from avid.policy.compaction import CompactReport
from avid.runtime import hooks
from avid.runtime.loop import RoundLimitExceeded, agent_loop
from avid.tools import TOOLS

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def make_turn(text="", tool_calls=(), finish_reason="stop"):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = list(tool_calls)
    return Turn(
        message=message,
        text=text,
        tool_calls=list(tool_calls),
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="m",
        finish_reason=finish_reason,
    )


def tool_call(name, arguments="{}", call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class FakeChat:
    """按顺序返回预设轮次，并记录每轮收到的参数。"""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.turns[len(self.requests) - 1]


# ---------- 循环基本行为 ----------


def test_returns_text_and_appends_assistant_when_no_tool_calls(hook_registry):
    chat = FakeChat(make_turn("你好"))
    messages = [{"role": "user", "content": "hi"}]

    assert agent_loop(messages, config=CONFIG, chat=chat) == "你好"
    assert messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "你好"},
    ]
    assert "Act, don't explain." in chat.requests[0]["system"]
    assert (
        "Use load_skill to read the full instructions when a skill applies."
        in chat.requests[0]["system"]
    )
    assert chat.requests[0]["tools"] == TOOLS
    assert all(m["role"] != "system" for m in messages)


def test_executes_tool_call_then_finishes():
    seen = []

    def read_file(args, **kwargs):
        seen.append(args)
        return "文件内容"

    chat = FakeChat(
        make_turn(
            "", [tool_call("read_file", '{"path": "a.txt"}')], finish_reason="tool_calls"
        ),
        make_turn("读到了"),
    )
    messages = [{"role": "user", "content": "读 a.txt"}]

    result = agent_loop(
        messages, config=CONFIG, chat=chat, registry={"read_file": read_file}
    )

    assert result == "读到了"
    assert seen == [{"path": "a.txt"}]
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "文件内容",
    }
    assert chat.requests[1]["messages"][-1]["role"] == "tool"


def test_multiple_tool_calls_become_multiple_tool_messages():
    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call("read_file", "{}", "call_1"),
                tool_call("read_file", "{}", "call_2"),
            ],
        ),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "读两个"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "内容"})

    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == [
        "call_1",
        "call_2",
    ]


def test_unknown_tool_is_reported_back_to_the_model():
    chat = FakeChat(
        make_turn("", [tool_call("run_bash", '{"command": "ls"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "ls"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={})

    assert messages[2]["content"] == "未知工具：run_bash"


def test_tool_exception_becomes_a_result_not_a_crash():
    def boom(args, **kwargs):
        raise ValueError("权限不足")

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": boom})

    assert "权限不足" in messages[2]["content"]


def test_invalid_json_arguments_are_reported():
    chat = FakeChat(make_turn("", [tool_call("read_file", "{不是 json")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "不是合法 JSON" in messages[2]["content"]


def test_non_string_tool_result_is_serialised():
    chat = FakeChat(make_turn("", [tool_call("stat")]), make_turn("好的"))
    messages = [{"role": "user", "content": "看"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"stat": lambda a: {"lines": 3}})

    assert messages[2]["content"] == '{"lines": 3}'


def test_round_limit_raises_instead_of_returning_partial_text():
    chat = FakeChat(*[make_turn("还在调工具", [tool_call("read_file")]) for _ in range(3)])
    messages = [{"role": "user", "content": "读"}]

    with pytest.raises(RoundLimitExceeded):
        agent_loop(
            messages,
            config=CONFIG,
            chat=chat,
            registry={"read_file": lambda a: "内容"},
            max_rounds=3,
        )


# ---------- ② PreToolUse ----------


def test_pre_tool_use_block_skips_the_handler(hook_registry):
    executed = []

    def read_file(args, **kwargs):
        executed.append(args)
        return "内容"

    hook_registry.register("PreToolUse", lambda ctx: hooks.BLOCK)
    chat = FakeChat(
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(
        messages, config=CONFIG, chat=chat, registry={"read_file": read_file}
    )

    assert executed == []
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Permission denied.",
    }


def test_pre_tool_use_block_does_not_stop_the_loop(hook_registry):
    hook_registry.register("PreToolUse", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("那我换个说法"))
    messages = [{"role": "user", "content": "读"}]

    result = agent_loop(
        messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "内容"}
    )

    assert result == "那我换个说法"
    assert len(chat.requests) == 2


def test_pre_tool_use_receives_name_and_parsed_arguments(hook_registry):
    seen = []
    hook_registry.register("PreToolUse", lambda ctx: seen.append((ctx["tool"], ctx["arguments"])))

    chat = FakeChat(
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}')]), make_turn("好的")
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert seen == [("read_file", {"path": "a.txt"})]


def test_pre_tool_use_sees_the_round_number(hook_registry):
    rounds = []
    hook_registry.register("PreToolUse", lambda ctx: rounds.append(ctx["round"]))

    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("", [tool_call("read_file", "{}", "call_2")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert rounds == [1, 2]


def test_unknown_tool_never_reaches_pre_tool_use(hook_registry):
    def spy(ctx):
        raise AssertionError("未知工具不该触发 PreToolUse")

    hook_registry.register("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("nope")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={})

    assert messages[2]["content"] == "未知工具：nope"


def test_unparsable_arguments_never_reach_pre_tool_use(hook_registry):
    def spy(ctx):
        raise AssertionError("JSON 解析失败不该触发 PreToolUse")

    hook_registry.register("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("read_file", "{坏 json")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "不是合法 JSON" in messages[2]["content"]


def test_non_object_arguments_never_reach_pre_tool_use(hook_registry):
    def spy(ctx):
        raise AssertionError("非对象参数不该触发 PreToolUse")

    hook_registry.register("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("read_file", "[1, 2]")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "JSON 对象" in messages[2]["content"]


def test_denials_still_count_towards_the_round_limit(hook_registry):
    hook_registry.register("PreToolUse", lambda ctx: hooks.BLOCK)
    chat = FakeChat(*[make_turn("", [tool_call("read_file")]) for _ in range(3)])
    messages = [{"role": "user", "content": "读"}]

    with pytest.raises(RoundLimitExceeded):
        agent_loop(
            messages,
            config=CONFIG,
            chat=chat,
            registry={"read_file": lambda a: "内容"},
            max_rounds=3,
        )


# ---------- ③ PostToolUse ----------


def test_post_tool_use_can_rewrite_the_result(hook_registry):
    def rewrite(ctx):
        ctx["content"] = "改写过的结果"
        return

    hook_registry.register("PostToolUse", rewrite)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a, **kwargs: "原始"})

    assert messages[2]["content"] == "改写过的结果"


def test_post_tool_use_sees_the_raw_content(hook_registry):
    seen = []
    hook_registry.register("PostToolUse", lambda ctx: seen.append(ctx["content"]))

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a, **kwargs: "原始"})

    assert seen == ["原始"]


def test_large_output_hook_truncates_real_tool_output(hook_registry, monkeypatch):
    monkeypatch.setattr(hooks, "MAX_TOOL_OUTPUT_CHARS", 100)
    hook_registry.register("PostToolUse", hooks.large_output_hook)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x" * 300})

    assert "hook 按上下文预算截断" in messages[2]["content"]
    assert len(messages[2]["content"]) <= 100


# ---------- ① UserPromptSubmit ----------


def test_user_prompt_submit_injects_context(hook_registry):
    hook_registry.register(
        "UserPromptSubmit", lambda ctx: ctx["injected"].append("[环境] 测试注入")
    )
    chat = FakeChat(make_turn("好的"))
    messages = [{"role": "user", "content": "原始问题"}]

    agent_loop(messages, config=CONFIG, chat=chat)

    assert messages[0]["content"] == "[环境] 测试注入\n\n原始问题"
    assert chat.requests[0]["messages"][0]["content"] == "[环境] 测试注入\n\n原始问题"


def test_user_prompt_submit_receives_the_prompt(hook_registry):
    seen = []
    hook_registry.register("UserPromptSubmit", lambda ctx: seen.append(ctx["prompt"]))

    agent_loop([{"role": "user", "content": "问题"}], config=CONFIG, chat=FakeChat(make_turn("好")))

    assert seen == ["问题"]


def test_user_prompt_submit_can_block_the_model_call(hook_registry):
    hook_registry.register("UserPromptSubmit", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("不该被调用"))
    messages = [{"role": "user", "content": "问题"}]

    assert agent_loop(messages, config=CONFIG, chat=chat) == ""
    assert chat.requests == []


def test_user_prompt_submit_skipped_without_a_user_message(hook_registry):
    fired = []
    hook_registry.register("UserPromptSubmit", lambda ctx: fired.append(1))
    messages = [{"role": "assistant", "content": "之前的话"}]

    agent_loop(messages, config=CONFIG, chat=FakeChat(make_turn("嗯")))

    assert fired == []


# ---------- ④ Stop ----------


def test_stop_is_triggered_before_returning(hook_registry):
    seen = []
    hook_registry.register("Stop", lambda ctx: seen.append((ctx["rounds"], ctx["final_text"])))

    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=FakeChat(make_turn("答案")))

    assert seen == [(1, "答案")]


def test_stop_hook_can_block_exit_once(hook_registry):
    hook_registry.register("Stop", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("第一次"), make_turn("第二次"))
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "第二次"
    assert len(chat.requests) == 2


def test_stop_block_is_capped(hook_registry):
    hook_registry.register("Stop", lambda ctx: hooks.BLOCK)
    chat = FakeChat(*[make_turn(f"第{i}次") for i in range(5)])
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "第1次"
    assert len(chat.requests) == 2


def test_stop_nudge_is_appended_and_redirects_the_model(hook_registry):
    def stop(ctx):
        ctx["nudge"] = "别忘了给出结论"
        return hooks.BLOCK

    hook_registry.register("Stop", stop)
    chat = FakeChat(make_turn("草稿"), make_turn("结论"))
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "结论"
    assert messages[2] == {"role": "user", "content": "别忘了给出结论"}


def test_stop_block_disabled_when_budget_is_zero(hook_registry):
    hook_registry.register("Stop", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("唯一一轮"))

    result = agent_loop(
        [{"role": "user", "content": "x"}],
        config=CONFIG,
        chat=chat,
        max_stop_blocks=0,
    )

    assert result == "唯一一轮"
    assert len(chat.requests) == 1


def test_stop_receives_run_statistics(hook_registry):
    seen = []
    hook_registry.register("Stop", lambda ctx: seen.append((ctx["tool_calls"], ctx["denials"])))
    hook_registry.register("PreToolUse", lambda ctx: None)
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert seen == [(1, 0)]


# ---------- 拒绝信息回传给模型 ----------


def test_hard_deny_message_reaches_the_model(hook_registry):
    hook_registry.register("PreToolUse", hooks.permission_hook)
    executed = []
    chat = FakeChat(
        make_turn("", [tool_call("bash", '{"command": "rm -rf /"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "清理一下"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"bash": lambda a: executed.append(a) or "不该执行"},
    )

    assert executed == []
    assert "Permission denied." in messages[2]["content"]
    assert "永久禁止" in messages[2]["content"]


def test_hook_supplied_denied_content_is_used(hook_registry):
    def blocker(ctx):
        ctx["denied_content"] = "自定义拒绝文案"
        return hooks.BLOCK

    hook_registry.register("PreToolUse", blocker)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"] == "自定义拒绝文案"


def test_block_without_denied_content_falls_back_to_the_default(hook_registry):
    hook_registry.register("PreToolUse", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"] == "Permission denied."


# ---------- todo_write 与 reminder ----------


def test_system_prompt_asks_for_a_plan_first(hook_registry):
    chat = FakeChat(make_turn("好的"))

    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=chat)

    assert "todo_write" in chat.requests[0]["system"]


def test_todo_write_result_is_returned_to_the_model(hook_registry):
    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call(
                    "todo_write",
                    '{"todos": [{"content": "第一步", "status": "in_progress"}]}',
                )
            ],
        ),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat)

    assert "已更新 TODO" in messages[2]["content"]
    assert "第一步" in messages[2]["content"]


def test_todo_state_does_not_leak_between_runs(hook_registry):
    first = FakeChat(
        make_turn(
            "",
            [
                tool_call(
                    "todo_write",
                    '{"todos": [{"content": "任务A", "status": "pending"}]}',
                )
            ],
        ),
        make_turn("好了"),
    )
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=first)

    messages = [{"role": "user", "content": "x"}]
    second = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好了"))
    agent_loop(
        messages,
        config=CONFIG,
        chat=second,
        registry={"read_file": lambda a: "x"},
        todo_reminder_after=1,
    )

    reminder = next(
        m["content"]
        for m in messages
        if str(m.get("content", "")).startswith("[提醒]")
    )

    assert "列表为空" in reminder  # 上一次运行的"任务A"没有泄漏过来


def test_todo_write_resets_the_silence_counter(hook_registry):
    chat = FakeChat(
        *[
            make_turn("", [tool_call("todo_write", '{"todos": []}', f"c{i}")])
            for i in range(4)
        ],
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, todo_reminder_after=2)

    assert not [m for m in messages if str(m.get("content", "")).startswith("[提醒]")]


def test_reminder_is_injected_before_the_next_model_call(hook_registry):
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("", [tool_call("read_file", "{}", "c2")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
        todo_reminder_after=2,
    )

    reminders = [m for m in messages if str(m.get("content", "")).startswith("[提醒]")]

    assert len(reminders) == 1
    # 注入发生在第 3 次请求之前，所以第 3 次请求看得到它
    assert any(
        str(m.get("content", "")).startswith("[提醒]")
        for m in chat.requests[2]["messages"]
    )


def test_reminder_fires_once_per_silent_streak(hook_registry):
    chat = FakeChat(
        *[make_turn("", [tool_call("read_file", "{}", f"c{i}")]) for i in range(6)],
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
        todo_reminder_after=1,
        max_rounds=7,
    )

    assert (
        sum(1 for m in messages if str(m.get("content", "")).startswith("[提醒]")) == 1
    )


def test_reminder_rearms_after_a_todo_write(hook_registry):
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn(
            "",
            [
                tool_call(
                    "todo_write",
                    '{"todos": [{"content": "a", "status": "pending"}]}',
                )
            ],
        ),
        make_turn("", [tool_call("read_file", "{}", "c3")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
        todo_reminder_after=1,
    )

    assert (
        sum(1 for m in messages if str(m.get("content", "")).startswith("[提醒]")) == 2
    )


def test_default_threshold_does_not_fire_on_short_runs(hook_registry):
    chat = FakeChat(make_turn("直接答"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat)

    assert not [m for m in messages if str(m.get("content", "")).startswith("[提醒]")]


# ---------- 技能系统接入 ----------


def point_skills_at(tmp_path, monkeypatch):
    """把技能目录指到临时工作区。

    技能目录是"运行级工作区根 / skills"，且在**构造时**解析（P2-18 之前是 import 时
    绑定的模块常量）。所以这里换 cwd，而不是改模块常量——那个常量已经不存在了。
    """
    from avid.policy import skills as skill_loader

    monkeypatch.chdir(tmp_path)
    return skill_loader.default_skills_dir(tmp_path)


def write_skill(root, directory, text):
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")


def test_skill_catalog_reaches_the_model(hook_registry, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)
    write_skill(root, "demo", "---\ndescription: 演示技能\n---\n正文")

    chat = FakeChat(make_turn("好的"))
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=chat)

    assert "- demo: 演示技能" in chat.requests[0]["system"]


def test_catalog_changes_are_picked_up_on_the_next_run(hook_registry, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)

    first = FakeChat(make_turn("好的"))
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=first)
    assert "demo" not in first.requests[0]["system"]

    write_skill(root, "demo", "---\ndescription: 新加的\n---\n正文")

    second = FakeChat(make_turn("好的"))
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=second)
    assert "- demo: 新加的" in second.requests[0]["system"]


def test_load_skill_returns_the_full_text_as_tool_result(hook_registry, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)
    write_skill(root, "demo", "---\ndescription: 演示\n---\n这是技能的全文。")

    chat = FakeChat(
        make_turn("", [tool_call("load_skill", '{"name": "demo"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat)

    assert messages[2]["role"] == "tool"
    assert "这是技能的全文。" in messages[2]["content"]


def test_load_skill_is_not_in_the_permission_gate(hook_registry, tmp_path, monkeypatch):
    from avid.policy.permission import APPROVAL_RULES

    assert "load_skill" not in APPROVAL_RULES


def test_unknown_skill_returns_error_text_without_raising(hook_registry, tmp_path, monkeypatch):
    """验收项：未知技能名返回错误文本且不抛出异常。"""
    point_skills_at(tmp_path, monkeypatch)

    chat = FakeChat(
        make_turn("", [tool_call("load_skill", '{"name": "nope"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat)

    assert messages[2]["content"] == "Error: Unknown skill 'nope'. Available: none"
    assert result == "好的"


# ---------- 压缩管线接入 ----------
#
# 编排细节（步骤顺序、阈值、一次性标志）在 tests/test_context.py 里测；
# 这里只验循环确实把管线接上了、兜底重试只做一次、以及状态不跨运行泄漏。


def test_context_pipeline_runs_before_every_model_call(hook_registry, monkeypatch):
    rounds = []
    monkeypatch.setattr(
        "avid.runtime.context.prepare",
        lambda transcript, state, **kwargs: rounds.append(state.round),
    )

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    agent_loop(
        [{"role": "user", "content": "x"}],
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
    )

    assert rounds == [1, 2]


def test_prompt_too_long_triggers_one_reactive_retry(hook_registry, monkeypatch):
    from avid.ai.client import PromptTooLongError

    calls = {"chat": 0, "reactive": 0}

    def fake_chat(config, messages, **kwargs):
        calls["chat"] += 1
        if calls["chat"] == 1:
            raise PromptTooLongError("超了")
        return make_turn("好的")

    def fake_reactive(transcript, state, **kwargs):
        calls["reactive"] += 1
        transcript.replace_all([{"role": "user", "content": "[历史摘要] 压缩过了"}])
        return CompactReport("reactive_compact", "摘要更早的 3 条", 999, 10)

    monkeypatch.setattr("avid.runtime.context.reactive", fake_reactive)
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=fake_chat)

    assert result == "好的"
    assert calls == {"chat": 2, "reactive": 1}
    assert "历史摘要" in messages[0]["content"]


def test_reactive_is_not_retried_twice(hook_registry, monkeypatch):
    from avid.ai.client import PromptTooLongError

    calls = {"chat": 0, "reactive": 0}

    def always_too_long(config, messages, **kwargs):
        calls["chat"] += 1
        raise PromptTooLongError("还是超")

    def fake_reactive(transcript, state, **kwargs):
        calls["reactive"] += 1
        return CompactReport("reactive_compact", "摘要", 999, 10)

    monkeypatch.setattr("avid.runtime.context.reactive", fake_reactive)

    with pytest.raises(PromptTooLongError):
        agent_loop(
            [{"role": "user", "content": "x"}], config=CONFIG, chat=always_too_long
        )

    assert calls == {"chat": 2, "reactive": 1}


def test_reactive_retry_sends_the_compressed_history(hook_registry, monkeypatch):
    """重试必须拿压缩后的历史再发一次，不能把旧的原样重发。"""
    from avid.ai.client import PromptTooLongError

    seen = []

    def fake_chat(config, messages, **kwargs):
        seen.append([m["content"] for m in messages])
        if len(seen) == 1:
            raise PromptTooLongError("超了")
        return make_turn("好的")

    def fake_reactive(transcript, state, **kwargs):
        transcript.replace_all([{"role": "user", "content": "压缩后的历史"}])
        return CompactReport("reactive_compact", "摘要", 999, 10)

    monkeypatch.setattr("avid.runtime.context.reactive", fake_reactive)

    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=fake_chat)

    assert seen[0] == ["x"]
    assert seen[1] == ["压缩后的历史"]


def test_compaction_is_logged(hook_registry, monkeypatch, caplog):
    monkeypatch.setattr(
        "avid.policy.compaction.tool_result_budget",
        lambda transcript, **kwargs: CompactReport(
            "tool_result_budget", "落盘 2 项", 300, 100
        ),
    )

    with caplog.at_level("INFO", logger="avid.runtime.context"):
        agent_loop(
            [{"role": "user", "content": "x"}],
            config=CONFIG,
            chat=FakeChat(make_turn("好的")),
        )

    assert any(
        "compact: tool_result_budget" in record.getMessage() for record in caplog.records
    )


def test_compaction_count_reaches_the_stop_hook(hook_registry, monkeypatch):
    monkeypatch.setattr(
        "avid.policy.compaction.snip_compact",
        lambda transcript, **kwargs: CompactReport(
            "snip_compact", "裁掉中间 10 条", 60, 30
        ),
    )
    seen = []
    hook_registry.register("Stop", lambda ctx: seen.append(ctx["compactions"]))

    agent_loop(
        [{"role": "user", "content": "x"}],
        config=CONFIG,
        chat=FakeChat(make_turn("好的")),
    )

    assert seen == [1]


def test_run_state_is_created_per_run(hook_registry, monkeypatch):
    """两次运行各有自己的 RunState——状态不跨运行泄漏。"""
    states = []

    def fake_prepare(transcript, state, **kwargs):
        states.append(state)
        return

    monkeypatch.setattr("avid.runtime.context.prepare", fake_prepare)

    agent_loop(
        [{"role": "user", "content": "a"}],
        config=CONFIG,
        chat=FakeChat(make_turn("好")),
    )
    agent_loop(
        [{"role": "user", "content": "b"}],
        config=CONFIG,
        chat=FakeChat(make_turn("好")),
    )

    assert len(states) == 2
    assert states[0] is not states[1]
