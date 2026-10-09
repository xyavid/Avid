import threading
import time
from dataclasses import replace

import pytest
from support import run_loop

from avid.agent.compaction import CompactReport
from avid.agent.hooks import BLOCK, large_output_hook, permission_hook
from avid.agent.state import MAX_CONSECUTIVE_DENIALS
from avid.agent.tools import TOOLS
from avid.providers.client import Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def make_turn(text="", tool_calls=(), finish_reason="stop", reasoning="", usage=None):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = list(tool_calls)
    return Turn(
        message=message,
        text=text,
        tool_calls=list(tool_calls),
        usage=usage or Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="m",
        finish_reason=finish_reason,
        reasoning=reasoning,
    )


def tool_call(name, arguments=None, call_id="call_1"):
    from support import PLACEHOLDER_ARGS

    if arguments is None:
        arguments = PLACEHOLDER_ARGS.get(name, "{}")
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class FakeChat:
    """Returns preset turns in order and records the arguments of every call."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.turns[len(self.requests) - 1]


# ---------- basic loop behavior ----------


def test_returns_text_and_appends_assistant_when_no_tool_calls(hook_registry):
    chat = FakeChat(make_turn("你好"))
    messages = [{"role": "user", "content": "hi"}]

    assert run_loop(messages, config=CONFIG, chat=chat) == "你好"
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

    result = run_loop(
        messages, config=CONFIG, chat=chat, registry={"read_file": read_file}
    )

    assert result == "读到了"
    assert seen == [{"path": "a.txt"}]
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "文件内容",
    }
    # the request tail is the per-round context block (user role, not persisted)
    assert chat.requests[1]["messages"][-2]["role"] == "tool"
    assert chat.requests[1]["messages"][-1]["role"] == "user"
    assert chat.requests[1]["messages"][-1]["content"].startswith("[上下文]")


def test_multiple_tool_calls_become_multiple_tool_messages():
    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call("read_file", '{"path": "a.txt"}', "call_1"),
                tool_call("read_file", '{"path": "a.txt"}', "call_2"),
            ],
        ),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "读两个"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "内容"})

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

    run_loop(messages, config=CONFIG, chat=chat, registry={})

    assert messages[2]["content"] == "未知工具：run_bash"


def test_tool_exception_becomes_a_result_not_a_crash():
    def boom(args, **kwargs):
        raise ValueError("权限不足")

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": boom})

    assert "权限不足" in messages[2]["content"]


def test_tool_failure_kinds_have_distinguishable_prefixes():
    """The three failure kinds carry distinguishable prefixes because web/schemas.py classifies
    tool status from them: they are an external contract, not wording preference.
    """

    def boom(args, **kwargs):
        raise ValueError("磁盘满了")

    # program / environment error
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]
    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": boom})
    assert messages[2]["content"].startswith("工具执行失败：read_file（磁盘满了）")
    assert "不要用同样的参数重复调用" in messages[2]["content"]

    # business refusal: the tool's own error text passes through with no extra prefix
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]
    run_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda args, **kwargs: "错误：文件不存在"},
    )
    assert messages[2]["content"] == "错误：文件不存在"


def test_invalid_json_arguments_are_reported():
    chat = FakeChat(make_turn("", [tool_call("read_file", "{不是 json")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"].startswith("参数错误：")
    assert "不是合法 JSON" in messages[2]["content"]


def test_non_string_tool_result_is_serialised():
    chat = FakeChat(make_turn("", [tool_call("stat")]), make_turn("好的"))
    messages = [{"role": "user", "content": "看"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"stat": lambda a: {"lines": 3}})

    assert messages[2]["content"] == '{"lines": 3}'


def test_a_plain_task_finishes_no_matter_how_many_rounds_it_takes():
    """No round cap exists: 12 tool rounds still run to the model's own finish (regression
    against the old hard-coded limit of 8, which raised RoundLimitExceeded).
    """
    chat = FakeChat(
        *[make_turn("读一个", [tool_call("read_file")]) for _ in range(12)],
        make_turn("都读完了"),
    )
    messages = [{"role": "user", "content": "读 12 个文件"}]

    assert (
        run_loop(
            messages,
            config=CONFIG,
            chat=chat,
            registry={"read_file": lambda a: "内容"},
        )
        == "都读完了"
    )
    # all 12 rounds really ran: the model is called again each round
    assert len(chat.requests) == 13


def test_one_turn_with_several_safe_calls_runs_them_concurrently():
    """Three read_file calls in one turn run concurrently: threading.Barrier(3) times out
    unless all three handlers overlap.
    """
    barrier = threading.Barrier(3)
    seen: list[str] = []

    def reader(arguments, **kwargs):
        seen.append(str(arguments.get("path")))
        barrier.wait(timeout=3)
        return f"内容:{arguments['path']}"

    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call("read_file", '{"path": "a.txt"}', "c1"),
                tool_call("read_file", '{"path": "b.txt"}', "c2"),
                tool_call("read_file", '{"path": "c.txt"}', "c3"),
            ],
        ),
        make_turn("读完"),
    )
    messages = [{"role": "user", "content": "读三个文件"}]

    assert (
        run_loop(
            messages,
            config=CONFIG,
            chat=chat,
            registry={"read_file": reader},
        )
        == "读完"
    )
    assert sorted(seen) == ["a.txt", "b.txt", "c.txt"]
    # result messages keep source order in the transcript (concurrency does not reorder it)
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2", "c3"]
    assert [m["content"] for m in tool_messages] == [
        "内容:a.txt",
        "内容:b.txt",
        "内容:c.txt",
    ]


def test_max_parallel_tools_one_restores_strictly_serial_dispatch():
    """With max_parallel_tools=1 same-turn calls run one by one (also an evaluation baseline)."""
    order: list[str] = []

    def reader(arguments, **kwargs):
        order.append(str(arguments.get("path")))
        return "ok"

    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call("read_file", '{"path": "a.txt"}', "c1"),
                tool_call("read_file", '{"path": "b.txt"}', "c2"),
            ],
        ),
        make_turn("好"),
    )

    run_loop(
        [{"role": "user", "content": "读"}],
        config=CONFIG,
        chat=chat,
        registry={"read_file": reader},
        max_parallel_tools=1,
    )

    assert order == ["a.txt", "b.txt"]


def test_exclusive_writes_in_one_turn_never_overlap_reads(hook_registry):
    """A write is a barrier inside a turn: it must not overlap the reads around it (a corrupt
    file is worse than latency).
    """
    # No tool registry here: the default permission hook would block write_file on approval.
    spans: list[tuple[str, float, float]] = []
    lock = threading.Lock()

    def span(label, delay=0.05):
        def run(arguments, **kwargs):
            start = time.monotonic()
            time.sleep(delay)
            with lock:
                spans.append((label, start, time.monotonic()))
            return "ok"

        return run

    registry = {
        "read_file": span("read"),
        "glob": span("glob"),
        "write_file": span("write"),
    }
    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call("read_file", '{"path": "a.txt"}', "c1"),
                tool_call("glob", call_id="c2"),
                tool_call("write_file", '{"path": "a.txt", "content": "x"}', "c3"),
                tool_call("read_file", '{"path": "a.txt"}', "c4"),
            ],
        ),
        make_turn("好"),
    )

    run_loop(
        [{"role": "user", "content": "改 a.txt"}],
        config=CONFIG,
        chat=chat,
        registry=registry,
    )

    def window(label):
        start, end = next((s, e) for name, s, e in spans if name == label)
        return start, end

    write_start, write_end = window("write")
    for label in ("read", "glob"):
        start, end = window(label)
        assert not (start < write_end and write_start < end), f"{label} 与写重叠了"


# ---------- ② PreToolUse ----------


def test_pre_tool_use_block_skips_the_handler(hook_registry):
    executed = []

    def read_file(args, **kwargs):
        executed.append(args)
        return "内容"

    hook_registry.register("PreToolUse", lambda ctx: BLOCK)
    chat = FakeChat(
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "读"}]

    run_loop(
        messages, config=CONFIG, chat=chat, registry={"read_file": read_file}
    )

    assert executed == []
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Permission denied.",
    }


def test_pre_tool_use_block_does_not_stop_the_loop(hook_registry):
    hook_registry.register("PreToolUse", lambda ctx: BLOCK)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("那我换个说法"))
    messages = [{"role": "user", "content": "读"}]

    result = run_loop(
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

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert seen == [("read_file", {"path": "a.txt"})]


def test_pre_tool_use_sees_the_round_number(hook_registry):
    rounds = []
    hook_registry.register("PreToolUse", lambda ctx: rounds.append(ctx["round"]))

    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}', "call_2")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert rounds == [1, 2]


def test_unknown_tool_never_reaches_pre_tool_use(hook_registry):
    def spy(ctx):
        raise AssertionError("未知工具不该触发 PreToolUse")

    hook_registry.register("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("nope")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={})

    assert messages[2]["content"] == "未知工具：nope"


def test_unparsable_arguments_never_reach_pre_tool_use(hook_registry):
    def spy(ctx):
        raise AssertionError("JSON 解析失败不该触发 PreToolUse")

    hook_registry.register("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("read_file", "{坏 json")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "不是合法 JSON" in messages[2]["content"]


def test_non_object_arguments_never_reach_pre_tool_use(hook_registry):
    def spy(ctx):
        raise AssertionError("非对象参数不该触发 PreToolUse")

    hook_registry.register("PreToolUse", spy)
    chat = FakeChat(make_turn("", [tool_call("read_file", "[1, 2]")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert "JSON 对象" in messages[2]["content"]


def test_denied_tool_calls_do_not_terminate_the_loop(hook_registry):
    """A denied call is neither failure nor endpoint: the result goes to the model as usual."""
    hook_registry.register("PreToolUse", lambda ctx: BLOCK)
    chat = FakeChat(
        make_turn("", [tool_call("read_file", call_id="c1")]),
        make_turn("", [tool_call("read_file", call_id="c2")]),
        make_turn("那我不读了"),
    )
    messages = [{"role": "user", "content": "读"}]

    assert (
        run_loop(
            messages,
            config=CONFIG,
            chat=chat,
            registry={"read_file": lambda a: "内容"},
        )
        == "那我不读了"
    )
    # both denial texts reach the context (the model sees the refusal, not an empty result)
    denied = [m for m in messages if m.get("role") == "tool"]
    assert len(denied) == 2
    assert all("Permission denied" in m["content"] for m in denied)


def test_consecutive_denials_stop_the_run(hook_registry):
    """A streak of denials with no success in between stops the run: the criterion is the streak,
    not the total (any successful call clears it, see the next case).
    """
    hook_registry.register("PreToolUse", lambda ctx: BLOCK)
    chat = FakeChat(
        *[
            make_turn("", [tool_call("read_file", call_id=f"c{i}")])
            for i in range(MAX_CONSECUTIVE_DENIALS + 2)
        ]
    )
    messages = [{"role": "user", "content": "读"}]

    text = run_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "内容"},
    )

    assert "停止" in text and "连续" in text
    # stops at the threshold: round MAX+1 is never asked, so extra turns are the failure signal
    assert len(chat.requests) == MAX_CONSECUTIVE_DENIALS
    assert messages[-1]["role"] == "assistant"
    assert "停止" in messages[-1]["content"]


def test_one_successful_call_clears_the_denial_streak(hook_registry):
    """Alternating denied/passed calls is not a streak: occasional denials must not fail a run."""
    counter = {"n": 0}

    def roughly(context):
        counter["n"] += 1
        return BLOCK if counter["n"] % 2 else None

    hook_registry.register("PreToolUse", roughly)
    chat = FakeChat(
        *[
            make_turn("", [tool_call("read_file", call_id=f"c{i}")])
            for i in range(2 * MAX_CONSECUTIVE_DENIALS + 2)
        ],
        make_turn("做完了"),
    )
    messages = [{"role": "user", "content": "读"}]

    text = run_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "内容"},
    )

    assert text == "做完了"


# ---------- A1: blank or length-truncated answers are not "done" ----------


def test_blank_answer_notice_names_the_reason(hook_registry):
    """The notice must name why there is no visible text instead of a generic blank answer:
    the turn's reasoning characters and tokens make that reason computable.
    """
    skipped = make_turn("", finish_reason="length", reasoning="先看目录，再读文件。" * 2)

    chat = FakeChat(skipped, skipped)
    messages = [{"role": "user", "content": "做"}]

    text = run_loop(messages, config=CONFIG, chat=chat)

    assert "思维链" in text
    assert f"{len(skipped.reasoning)} 字符" in text
    # the nudge carries the reason too: the model must know it stopped after thinking
    nudge = [m for m in messages if m.get("role") == "user" and "可见正文" in m["content"]]
    assert len(nudge) == 1
    assert "思维链" in nudge[0]["content"]


def test_blank_answer_notice_reports_reasoning_tokens(hook_registry):
    """When reasoning tokens are reported, the notice prints them so the spend is visible."""
    spend = make_turn("", finish_reason="length")
    spend = replace(spend, usage=Usage(1, 900, 901, reasoning_tokens=880))

    chat = FakeChat(spend, spend)
    text = run_loop([{"role": "user", "content": "做"}], config=CONFIG, chat=chat)

    assert "880" in text


def test_empty_answer_is_not_accepted_as_final(hook_registry):
    """A round with no visible text is not final: it counts as one Stop block and the nudge asks
    for a visible reply.
    """
    chat = FakeChat(
        make_turn("", finish_reason="length"),
        make_turn("环境探测完成：无显示、无外网。"),
    )
    messages = [{"role": "user", "content": "做"}]

    assert run_loop(messages, config=CONFIG, chat=chat) == "环境探测完成：无显示、无外网。"
    assert len(chat.requests) == 2
    # the nudge rides the Stop-nudge channel (same budget, event and message exit)
    nudges = [m for m in messages if m.get("role") == "user" and "可见" in m["content"]]
    assert len(nudges) == 1
    # the truncated round stays in context: the model must see it never finished
    assert messages.count(messages[1]) == 1


def test_still_blank_after_the_nudge_ends_with_a_visible_notice(hook_registry):
    """Still blank after the nudge: end with a visible notice, never return an empty string."""
    chat = FakeChat(make_turn("", finish_reason="length"), make_turn(""))
    messages = [{"role": "user", "content": "做"}]

    text = run_loop(messages, config=CONFIG, chat=chat)

    assert text.strip(), "空答复不能算运行成功"
    assert messages[-1]["role"] == "assistant"
    assert "可见答复" in messages[-1]["content"]


# ---------- ③ PostToolUse ----------


def test_post_tool_use_can_rewrite_the_result(hook_registry):
    def rewrite(ctx):
        ctx["content"] = "改写过的结果"
        return

    hook_registry.register("PostToolUse", rewrite)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a, **kwargs: "原始"})

    assert messages[2]["content"] == "改写过的结果"


def test_post_tool_use_sees_the_raw_content(hook_registry):
    seen = []
    hook_registry.register("PostToolUse", lambda ctx: seen.append(ctx["content"]))

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a, **kwargs: "原始"})

    assert seen == ["原始"]


def test_large_output_hook_truncates_real_tool_output(hook_registry, monkeypatch):
    monkeypatch.setattr("avid.agent.hooks.MAX_TOOL_OUTPUT_CHARS", 100)
    hook_registry.register("PostToolUse", large_output_hook)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x" * 300})

    assert "hook 按上下文预算截断" in messages[2]["content"]
    assert len(messages[2]["content"]) <= 100


# ---------- ① UserPromptSubmit ----------


def test_user_prompt_submit_injects_context(hook_registry):
    """Injected context goes into the system prompt while the user message keeps its original
    text: kernel-written environment info must never read as user speech.
    """
    hook_registry.register(
        "UserPromptSubmit", lambda ctx: ctx["injected"].append("[环境] 测试注入")
    )
    chat = FakeChat(make_turn("好的"))
    messages = [{"role": "user", "content": "原始问题"}]

    run_loop(messages, config=CONFIG, chat=chat)

    assert messages[0]["content"] == "原始问题"
    assert chat.requests[0]["messages"][0]["content"] == "原始问题"
    assert "[环境] 测试注入" in chat.requests[0]["system"]


def test_environment_block_lists_the_runs_tools(hook_registry):
    """The "available tools" list in the system prompt must be the run's own list: rendering
    from the global registry would advertise subagent to a subagent that cannot call it.
    """
    chat = FakeChat(make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]
    only_read = [item for item in TOOLS if item["function"]["name"] == "read_file"]

    run_loop(
        messages,
        config=CONFIG,
        chat=chat,
        tools=only_read,
        registry={"read_file": lambda a: "内容"},
    )

    system = chat.requests[0]["system"]
    assert "可用工具：read_file" in system
    assert "subagent" not in system


def test_user_prompt_submit_receives_the_prompt(hook_registry):
    seen = []
    hook_registry.register("UserPromptSubmit", lambda ctx: seen.append(ctx["prompt"]))

    run_loop([{"role": "user", "content": "问题"}], config=CONFIG, chat=FakeChat(make_turn("好")))

    assert seen == ["问题"]


def test_user_prompt_submit_can_block_the_model_call(hook_registry):
    hook_registry.register("UserPromptSubmit", lambda ctx: BLOCK)
    chat = FakeChat(make_turn("不该被调用"))
    messages = [{"role": "user", "content": "问题"}]

    assert run_loop(messages, config=CONFIG, chat=chat) == ""
    assert chat.requests == []


def test_user_prompt_submit_skipped_without_a_user_message(hook_registry):
    fired = []
    hook_registry.register("UserPromptSubmit", lambda ctx: fired.append(1))
    messages = [{"role": "assistant", "content": "之前的话"}]

    run_loop(messages, config=CONFIG, chat=FakeChat(make_turn("嗯")))

    assert fired == []


# ---------- ④ Stop ----------


def test_stop_is_triggered_before_returning(hook_registry):
    seen = []
    hook_registry.register("Stop", lambda ctx: seen.append((ctx["rounds"], ctx["final_text"])))

    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=FakeChat(make_turn("答案")))

    assert seen == [(1, "答案")]


def test_stop_hook_can_block_exit_once(hook_registry):
    hook_registry.register("Stop", lambda ctx: BLOCK)
    chat = FakeChat(make_turn("第一次"), make_turn("第二次"))
    messages = [{"role": "user", "content": "x"}]

    result = run_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "第二次"
    assert len(chat.requests) == 2


def test_stop_block_is_capped(hook_registry):
    hook_registry.register("Stop", lambda ctx: BLOCK)
    chat = FakeChat(*[make_turn(f"第{i}次") for i in range(5)])
    messages = [{"role": "user", "content": "x"}]

    result = run_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "第1次"
    assert len(chat.requests) == 2


def test_stop_nudge_is_appended_and_redirects_the_model(hook_registry):
    def stop(ctx):
        ctx["nudge"] = "别忘了给出结论"
        return BLOCK

    hook_registry.register("Stop", stop)
    chat = FakeChat(make_turn("草稿"), make_turn("结论"))
    messages = [{"role": "user", "content": "x"}]

    result = run_loop(messages, config=CONFIG, chat=chat, max_stop_blocks=1)

    assert result == "结论"
    assert messages[2] == {"role": "user", "content": "别忘了给出结论"}


def test_stop_block_disabled_when_budget_is_zero(hook_registry):
    hook_registry.register("Stop", lambda ctx: BLOCK)
    chat = FakeChat(make_turn("唯一一轮"))

    result = run_loop(
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

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert seen == [(1, 0)]


# ---------- denial text travels back to the model ----------


def test_hard_deny_message_reaches_the_model(hook_registry):
    hook_registry.register("PreToolUse", permission_hook)
    executed = []
    chat = FakeChat(
        make_turn("", [tool_call("bash", '{"command": "rm -rf /"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "清理一下"}]

    run_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"bash": lambda a: executed.append(a) or "不该执行"},
    )

    assert executed == []
    assert "Permission denied." in messages[2]["content"]
    # Denial text with no ask channel: retry after user confirmation in the UI, not a permanent
    # ban — destructive no longer means blacklisted forever.
    assert "没有可用的询问通道" in messages[2]["content"]


def test_hook_supplied_denied_content_is_used(hook_registry):
    def blocker(ctx):
        ctx["denied_content"] = "自定义拒绝文案"
        return BLOCK

    hook_registry.register("PreToolUse", blocker)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"] == "自定义拒绝文案"


def test_block_without_denied_content_falls_back_to_the_default(hook_registry):
    hook_registry.register("PreToolUse", lambda ctx: BLOCK)
    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    messages = [{"role": "user", "content": "读"}]

    run_loop(messages, config=CONFIG, chat=chat, registry={"read_file": lambda a: "x"})

    assert messages[2]["content"] == "Permission denied."


# ---------- todo_write and the reminder ----------


def test_system_prompt_asks_for_a_plan_first(hook_registry):
    chat = FakeChat(make_turn("好的"))

    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=chat)

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

    run_loop(messages, config=CONFIG, chat=chat)

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
    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=first)

    messages = [{"role": "user", "content": "x"}]
    second = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好了"))
    run_loop(
        messages,
        config=CONFIG,
        chat=second,
        registry={"read_file": lambda a: "x"},
    )

    # the plan block renders this run's TodoList only: no previous-run todo may appear anywhere
    assert all("任务A" not in str(m.get("content", "")) for m in messages)
    assert all(
        "任务A" not in str(m.get("content", ""))
        for request in second.requests
        for m in request["messages"]
    )


def test_plan_block_follows_the_todo_list(hook_registry):
    """The plan block rides the request tail, follows todo_write live, never enters messages."""
    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call(
                    "todo_write",
                    '{"todos": [{"content": "第一步", "status": "in_progress"}]}',
                    "c1",
                )
            ],
        ),
        make_turn("", [tool_call("todo_write", '{"todos": []}', "c2")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    run_loop(messages, config=CONFIG, chat=chat)

    first = chat.requests[0]["messages"][-1]["content"]
    second = chat.requests[1]["messages"][-1]["content"]
    third = chat.requests[2]["messages"][-1]["content"]
    assert first.startswith("[上下文]")
    assert "当前计划" not in first, "提交前没有计划块"
    assert "当前计划" in second and "第一步" in second and "[~] 1. 第一步" in second
    assert "当前计划" not in third, "清空后计划块整个不渲染"
    # the tail is not persisted: no kernel-written plan appears in the message channel
    assert not [m for m in messages if str(m.get("content", "")).startswith("[上下文]")]


def test_plan_is_visible_to_the_model_on_every_round(hook_registry):
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn(
            "",
            [
                tool_call(
                    "todo_write",
                    '{"todos": [{"content": "a", "status": "pending"}]}',
                    "c2",
                )
            ],
        ),
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}', "c3")]),
        make_turn("好了"),
    )
    messages = [{"role": "user", "content": "x"}]

    run_loop(messages, config=CONFIG, chat=chat)

    # every request after submission carries the plan in its tail (no every-N-rounds reminder)
    assert "当前计划" not in chat.requests[0]["messages"][-1]["content"]
    for request in chat.requests[2:]:
        tail = request["messages"][-1]["content"]
        assert "当前计划" in tail and "1. a" in tail


# ---------- skill system wiring ----------


def point_skills_at(tmp_path, monkeypatch):
    """Point the skills directory at a temp workspace: it resolves as "<workspace root>/skills"
    at construction time, so this swaps cwd instead of a module constant.
    """
    from avid.agent import skills as skill_loader

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
    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=chat)

    assert "- demo: 演示技能" in chat.requests[0]["system"]


def test_catalog_changes_are_picked_up_on_the_next_run(hook_registry, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)

    first = FakeChat(make_turn("好的"))
    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=first)
    assert "demo" not in first.requests[0]["system"]

    write_skill(root, "demo", "---\ndescription: 新加的\n---\n正文")

    second = FakeChat(make_turn("好的"))
    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=second)
    assert "- demo: 新加的" in second.requests[0]["system"]


def test_load_skill_returns_the_full_text_as_tool_result(hook_registry, tmp_path, monkeypatch):
    root = point_skills_at(tmp_path, monkeypatch)
    write_skill(root, "demo", "---\ndescription: 演示\n---\n这是技能的全文。")

    chat = FakeChat(
        make_turn("", [tool_call("load_skill", '{"name": "demo"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "x"}]

    run_loop(messages, config=CONFIG, chat=chat)

    assert messages[2]["role"] == "tool"
    assert "这是技能的全文。" in messages[2]["content"]


def test_load_skill_is_not_in_the_permission_gate(hook_registry, tmp_path, monkeypatch):
    """load_skill never enters the permission gate: it is in neither PATH_TOOLS nor WRITE_TOOLS,
    so the verdict is always auto-allow.
    """
    from avid.security.action import PATH_TOOLS, WRITE_TOOLS
    from avid.security.permission import brokerize, decide

    assert "load_skill" not in PATH_TOOLS | WRITE_TOOLS

    decision = decide(brokerize("load_skill", {"name": "demo"}))
    assert decision.allowed and decision.answered_by == "policy"


def test_unknown_skill_returns_error_text_without_raising(hook_registry, tmp_path, monkeypatch):
    point_skills_at(tmp_path, monkeypatch)

    chat = FakeChat(
        make_turn("", [tool_call("load_skill", '{"name": "nope"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "x"}]

    result = run_loop(messages, config=CONFIG, chat=chat)

    assert messages[2]["content"] == "错误：没有这个技能「nope」。可用：（无）"
    assert result == "好的"


# ---------- compaction pipeline wiring ----------
#
# Orchestration details (step order, thresholds, one-shot flags) live in tests/test_context.py;
# here only the wiring, the single reactive retry and per-run state are checked.


def test_context_pipeline_runs_before_every_model_call(hook_registry, monkeypatch):
    rounds = []
    monkeypatch.setattr(
        "avid.agent.context.ContextManager._compact",
        lambda self: rounds.append(self.state.round) or [],
    )

    chat = FakeChat(make_turn("", [tool_call("read_file")]), make_turn("好的"))
    run_loop(
        [{"role": "user", "content": "x"}],
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
    )

    assert rounds == [1, 2]


def test_prompt_too_long_triggers_one_reactive_retry(hook_registry, monkeypatch):
    from avid.providers.client import PromptTooLongError

    calls = {"chat": 0, "reactive": 0}

    def fake_chat(config, messages, **kwargs):
        calls["chat"] += 1
        if calls["chat"] == 1:
            raise PromptTooLongError("超了")
        return make_turn("好的")

    def fake_reactive(self):
        calls["reactive"] += 1
        self.transcript.replace_all([{"role": "user", "content": "[历史摘要] 压缩过了"}])
        return CompactReport("reactive_compact", "摘要更早的 3 条", 999, 10)

    monkeypatch.setattr(
        "avid.agent.context.ContextManager.reactive", fake_reactive
    )
    messages = [{"role": "user", "content": "x"}]

    result = run_loop(messages, config=CONFIG, chat=fake_chat)

    assert result == "好的"
    assert calls == {"chat": 2, "reactive": 1}
    assert "历史摘要" in messages[0]["content"]


def test_reactive_is_not_retried_twice(hook_registry, monkeypatch):
    from avid.providers.client import PromptTooLongError

    calls = {"chat": 0, "reactive": 0}

    def always_too_long(config, messages, **kwargs):
        calls["chat"] += 1
        raise PromptTooLongError("还是超")

    def fake_reactive(self):
        calls["reactive"] += 1
        return CompactReport("reactive_compact", "摘要", 999, 10)

    monkeypatch.setattr(
        "avid.agent.context.ContextManager.reactive", fake_reactive
    )

    with pytest.raises(PromptTooLongError):
        run_loop(
            [{"role": "user", "content": "x"}], config=CONFIG, chat=always_too_long
        )

    assert calls == {"chat": 2, "reactive": 1}


def test_reactive_retry_sends_the_compressed_history(hook_registry, monkeypatch):
    """The retry must send the compressed history, never resend the old one unchanged."""
    from avid.providers.client import PromptTooLongError

    seen = []

    def fake_chat(config, messages, **kwargs):
        seen.append([m["content"] for m in messages])
        if len(seen) == 1:
            raise PromptTooLongError("超了")
        return make_turn("好的")

    def fake_reactive(self):
        self.transcript.replace_all([{"role": "user", "content": "压缩后的历史"}])
        return CompactReport("reactive_compact", "摘要", 999, 10)

    monkeypatch.setattr(
        "avid.agent.context.ContextManager.reactive", fake_reactive
    )

    run_loop([{"role": "user", "content": "x"}], config=CONFIG, chat=fake_chat)

    # the tail sits at the request end (not persisted), so the first message is the history
    assert seen[0][0] == "x"
    assert seen[1][0] == "压缩后的历史"


def test_compaction_is_logged(hook_registry, caplog):
    """Compaction emits one announce log line: the UI and the ledger share the same source."""
    from avid.agent.compaction import ContextBudget
    from avid.agent.events import CONTEXT_COMPACTED

    events = []
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("读完了"),
        make_turn("好"),
    )
    summarizer = FakeChat(make_turn("之前的要点"))

    def observer(event):
        events.append({"type": event.type})

    with caplog.at_level("INFO", logger="avid.agent.compaction"):
        run_loop(
            [{"role": "user", "content": "x" * 3000}],
            config=CONFIG,
            chat=chat,
            registry={"read_file": lambda arguments: "内容"},
            summarize=summarizer,
            budget=ContextBudget(
                context_chars=10, keep_recent_turns=1, from_window=False
            ),
            on_event=observer,
        )

    assert any(
        "compact: compact_history" in record.getMessage() for record in caplog.records
    )
    assert any(item["type"] == CONTEXT_COMPACTED for item in events)


def test_compaction_count_reaches_the_stop_hook(hook_registry):
    """The compaction count reaches the Stop hook via state.snapshot."""
    from avid.agent.compaction import ContextBudget

    seen = []
    hook_registry.register("Stop", lambda ctx: seen.append(ctx["compactions"]))

    run_loop(
        [{"role": "user", "content": "x" * 3000}],
        config=CONFIG,
        chat=FakeChat(
            make_turn("", [tool_call("read_file")]),
            make_turn("读完了"),
            make_turn("好"),
        ),
        registry={"read_file": lambda arguments: "内容"},
        summarize=FakeChat(make_turn("要点")),
        budget=ContextBudget(
            context_chars=10, keep_recent_turns=1, from_window=False
        ),
    )

    assert seen == [1]


def test_run_state_is_created_per_run(hook_registry, monkeypatch):
    """Each run gets its own RunState: state never leaks across runs."""
    states = []

    def fake_compact(self):
        states.append(self.state)
        return []

    monkeypatch.setattr(
        "avid.agent.context.ContextManager._compact", fake_compact
    )

    run_loop(
        [{"role": "user", "content": "a"}],
        config=CONFIG,
        chat=FakeChat(make_turn("好")),
    )
    run_loop(
        [{"role": "user", "content": "b"}],
        config=CONFIG,
        chat=FakeChat(make_turn("好")),
    )

    assert len(states) == 2
    assert states[0] is not states[1]


def test_injected_budget_lowers_the_compaction_threshold(tmp_path, hook_registry):
    """An injected budget lowers the compaction threshold (the only entry for single-variable
    comparisons): five 20k-char files push the transcript past it, while the default 400k
    threshold compacts nothing.
    """
    # The hook registry stays empty so character counts are computable: in production
    # large_output_hook truncates every tool result to 8000 chars first.
    from avid.agent.context import ContextBudget

    for index in range(5):
        (tmp_path / f"big{index}.txt").write_text("x" * 20_000, encoding="utf-8")
    turns = [
        make_turn(
            tool_calls=[tool_call("read_file", f'{{"path": "big{i}.txt"}}', f"c{i}")]
        )
        for i in range(5)
    ]

    def run(budget):
        events = []
        summaries = FakeChat(make_turn("[摘要] 早前的读取"))
        text = run_loop(
            [{"role": "user", "content": "把 5 份文件都读一遍"}],
            config=CONFIG,
            chat=FakeChat(*turns, make_turn("读完了")),
            summarize=summaries,
            workspace_root=str(tmp_path),
            budget=budget,
            on_event=events.append,
        )
        return text, [event for event in events if event.type == "context_compacted"]

    text, compacted = run(None)
    assert text == "读完了"
    assert compacted == [], "默认阈值下不该压缩（400k 远高于这份 transcript）"

    text, compacted = run(ContextBudget(context_chars=50_000, keep_recent_turns=2))
    assert text == "读完了"
    assert compacted, "注入更低阈值后应当压缩"
    assert compacted[0].data["before"] > compacted[0].data["after"]


def test_loop_does_not_cap_the_output_budget(hook_registry):
    """Main rounds send no max_tokens: the cap belongs to the provider."""
    chat = FakeChat(make_turn("你好"))

    run_loop([{"role": "user", "content": "hi"}], config=CONFIG, chat=chat)

    assert chat.requests[0].get("max_tokens") is None
