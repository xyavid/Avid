import pytest

from avid import hooks
from avid.agent import RoundLimitExceeded, agent_loop
from avid.config import Config
from avid.llm import Turn, Usage
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


@pytest.fixture
def no_hooks(monkeypatch):
    """清空注册表：只测循环本身，不掺默认回调的影响。"""
    monkeypatch.setattr(hooks, "HOOKS", {event: [] for event in hooks.EVENTS})


# ---------- 循环基本行为 ----------


def test_returns_text_and_appends_assistant_when_no_tool_calls(no_hooks):
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

    def read_file(args):
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
    def boom(args):
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


def test_pre_tool_use_block_skips_the_handler(no_hooks):
    executed = []

    def read_file(args):
        executed.append(args)
        return "内容"

    hooks.register_hook("PreToolUse", lambda ctx: hooks.BLOCK)
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


def test_pre_tool_use_block_does_not_stop_the_loop(no_hooks):
    hooks.register_hook("PreToolUse", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("那我换个说法"))
    messages = [{"role": "user", "content": "读"}]

    result = agent_loop(
        messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "内容"}
    )

    assert result == "那我换个说法"
    assert len(chat.requests) == 2


def test_pre_tool_use_receives_name_and_parsed_arguments(no_hooks):
    seen = []
    hooks.register_hook("PreToolUse", lambda ctx: seen.append((ctx["tool"], ctx["arguments"])))

    chat = FakeChat(
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}')]), make_turn("好的")
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert seen == [("read_file", {"path": "a.txt"})]


def test_pre_tool_use_sees_the_round_number(no_hooks):
    rounds = []
    hooks.register_hook("PreToolUse", lambda ctx: rounds.append(ctx["round"]))

    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("", [tool_call("read_file", "{}", "call_2")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert rounds == [1, 2]


def test_unknown_tool_never_reaches_pre_tool_use(no_hooks):
    def spy(ctx):
        raise AssertionError("未知工具不该触发 PreToolUse")

    hooks.register_hook("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("nope")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={})

    assert messages[2]["content"] == "未知工具：nope"


def test_unparsable_arguments_never_reach_pre_tool_use(no_hooks):
    def spy(ctx):
        raise AssertionError("JSON 解析失败不该触发 PreToolUse")

    hooks.register_hook("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("read_file", "{坏 json")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "不是合法 JSON" in messages[2]["content"]


def test_non_object_arguments_never_reach_pre_tool_use(no_hooks):
    def spy(ctx):
        raise AssertionError("非对象参数不该触发 PreToolUse")

    hooks.register_hook("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("read_file", "[1, 2]")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "JSON 对象" in messages[2]["content"]


def test_denials_still_count_towards_the_round_limit(no_hooks):
    hooks.register_hook("PreToolUse", lambda ctx: hooks.BLOCK)
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


def test_post_tool_use_can_rewrite_the_result(no_hooks):
    def rewrite(ctx):
        ctx["content"] = "改写过的结果"
        return None

    hooks.register_hook("PostToolUse", rewrite)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "原始"})

    assert messages[2]["content"] == "改写过的结果"


def test_post_tool_use_sees_the_raw_content(no_hooks):
    seen = []
    hooks.register_hook("PostToolUse", lambda ctx: seen.append(ctx["content"]))

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "原始"})

    assert seen == ["原始"]


def test_large_output_hook_truncates_real_tool_output(no_hooks, monkeypatch):
    monkeypatch.setattr(hooks, "MAX_TOOL_OUTPUT_CHARS", 100)
    hooks.register_hook("PostToolUse", hooks.large_output_hook)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x" * 300})

    assert "hook 按上下文预算截断" in messages[2]["content"]
    assert len(messages[2]["content"]) <= 100


# ---------- ① UserPromptSubmit ----------


def test_user_prompt_submit_injects_context(no_hooks):
    hooks.register_hook(
        "UserPromptSubmit", lambda ctx: ctx["injected"].append("[环境] 测试注入")
    )
    chat = FakeChat(make_turn("好的"))
    messages = [{"role": "user", "content": "原始问题"}]

    agent_loop(messages, config=CONFIG, chat=chat)

    assert messages[0]["content"] == "[环境] 测试注入\n\n原始问题"
    assert chat.requests[0]["messages"][0]["content"] == "[环境] 测试注入\n\n原始问题"


def test_user_prompt_submit_receives_the_prompt(no_hooks):
    seen = []
    hooks.register_hook("UserPromptSubmit", lambda ctx: seen.append(ctx["prompt"]))

    agent_loop([{"role": "user", "content": "问题"}], config=CONFIG, chat=FakeChat(make_turn("好")))

    assert seen == ["问题"]


def test_user_prompt_submit_can_block_the_model_call(no_hooks):
    hooks.register_hook("UserPromptSubmit", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("不该被调用"))
    messages = [{"role": "user", "content": "问题"}]

    assert agent_loop(messages, config=CONFIG, chat=chat) == ""
    assert chat.requests == []


def test_user_prompt_submit_skipped_without_a_user_message(no_hooks):
    fired = []
    hooks.register_hook("UserPromptSubmit", lambda ctx: fired.append(1))
    messages = [{"role": "assistant", "content": "之前的话"}]

    agent_loop(messages, config=CONFIG, chat=FakeChat(make_turn("嗯")))

    assert fired == []


# ---------- ④ Stop ----------


def test_stop_is_triggered_before_returning(no_hooks):
    seen = []
    hooks.register_hook("Stop", lambda ctx: seen.append((ctx["rounds"], ctx["final_text"])))

    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=FakeChat(make_turn("答案")))

    assert seen == [(1, "答案")]


def test_stop_hook_can_block_exit_once(no_hooks):
    hooks.register_hook("Stop", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("第一次"), make_turn("第二次"))
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "第二次"
    assert len(chat.requests) == 2


def test_stop_block_is_capped(no_hooks):
    hooks.register_hook("Stop", lambda ctx: hooks.BLOCK)
    chat = FakeChat(*[make_turn(f"第{i}次") for i in range(5)])
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "第1次"
    assert len(chat.requests) == 2


def test_stop_nudge_is_appended_and_redirects_the_model(no_hooks):
    def stop(ctx):
        ctx["nudge"] = "别忘了给出结论"
        return hooks.BLOCK

    hooks.register_hook("Stop", stop)
    chat = FakeChat(make_turn("草稿"), make_turn("结论"))
    messages = [{"role": "user", "content": "x"}]

    result = agent_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "结论"
    assert messages[2] == {"role": "user", "content": "别忘了给出结论"}


def test_stop_block_disabled_when_budget_is_zero(no_hooks):
    hooks.register_hook("Stop", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("唯一一轮"))

    result = agent_loop(
        [{"role": "user", "content": "x"}],
        config=CONFIG,
        chat=chat,
        max_stop_blocks=0,
    )

    assert result == "唯一一轮"
    assert len(chat.requests) == 1


def test_stop_receives_run_statistics(no_hooks):
    seen = []
    hooks.register_hook("Stop", lambda ctx: seen.append((ctx["tool_calls"], ctx["denials"])))
    hooks.register_hook("PreToolUse", lambda ctx: None)
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert seen == [(1, 0)]


# ---------- 拒绝信息回传给模型 ----------


def test_hard_deny_message_reaches_the_model(no_hooks):
    hooks.register_hook("PreToolUse", hooks.permission_hook)
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


def test_hook_supplied_denied_content_is_used(no_hooks):
    def blocker(ctx):
        ctx["denied_content"] = "自定义拒绝文案"
        return hooks.BLOCK

    hooks.register_hook("PreToolUse", blocker)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"] == "自定义拒绝文案"


def test_block_without_denied_content_falls_back_to_the_default(no_hooks):
    hooks.register_hook("PreToolUse", lambda ctx: hooks.BLOCK)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"] == "Permission denied."


# ---------- todo_write 与 reminder ----------


def test_system_prompt_asks_for_a_plan_first(no_hooks):
    chat = FakeChat(make_turn("好的"))

    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=chat)

    assert "todo_write" in chat.requests[0]["system"]


def test_todo_write_result_is_returned_to_the_model(no_hooks):
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


def test_todo_state_does_not_leak_between_runs(no_hooks):
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


def test_todo_write_resets_the_silence_counter(no_hooks):
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


def test_reminder_is_injected_before_the_next_model_call(no_hooks):
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


def test_reminder_fires_once_per_silent_streak(no_hooks):
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


def test_reminder_rearms_after_a_todo_write(no_hooks):
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


def test_default_threshold_does_not_fire_on_short_runs(no_hooks):
    chat = FakeChat(make_turn("直接答"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat)

    assert not [m for m in messages if str(m.get("content", "")).startswith("[提醒]")]


# ---------- 技能系统接入 ----------


def point_skills_at(tmp_path, monkeypatch):
    from avid import skill_loader

    monkeypatch.setattr(skill_loader, "SKILLS_DIR", tmp_path)
    return tmp_path


def write_skill(root, directory, text):
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")


def test_skill_catalog_reaches_the_model(no_hooks, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)
    write_skill(root, "demo", "---\ndescription: 演示技能\n---\n正文")

    chat = FakeChat(make_turn("好的"))
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=chat)

    assert "- demo: 演示技能" in chat.requests[0]["system"]


def test_catalog_changes_are_picked_up_on_the_next_run(no_hooks, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)

    first = FakeChat(make_turn("好的"))
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=first)
    assert "demo" not in first.requests[0]["system"]

    write_skill(root, "demo", "---\ndescription: 新加的\n---\n正文")

    second = FakeChat(make_turn("好的"))
    agent_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=second)
    assert "- demo: 新加的" in second.requests[0]["system"]


def test_load_skill_returns_the_full_text_as_tool_result(no_hooks, tmp_path, monkeypatch):
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


def test_load_skill_is_not_in_the_permission_gate(no_hooks, tmp_path, monkeypatch):
    from avid.permission import APPROVAL_RULES

    assert "load_skill" not in APPROVAL_RULES


def test_unknown_skill_returns_error_text_without_raising(no_hooks, tmp_path, monkeypatch):
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
