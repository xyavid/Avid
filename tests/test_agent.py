import pytest

from avid.agent import SYSTEM, RoundLimitExceeded, agent_loop
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


def test_returns_text_and_appends_assistant_when_no_tool_calls():
    chat = FakeChat(make_turn("你好"))
    messages = [{"role": "user", "content": "hi"}]

    assert agent_loop(messages, config=CONFIG, chat=chat) == "你好"
    assert messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "你好"},
    ]
    assert chat.requests[0]["system"] == SYSTEM
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


# ---------- 权限校验接入 ----------


def test_denied_tool_is_reported_and_not_executed():
    executed = []

    def read_file(args):
        executed.append(args)
        return "内容"

    chat = FakeChat(
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": read_file},
        check=lambda name, arguments: False,
    )

    assert executed == []
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Permission denied.",
    }


def test_denied_tool_does_not_stop_the_loop():
    chat = FakeChat(
        make_turn("", [tool_call("read_file")]),
        make_turn("那我换个说法"),
    )
    messages = [{"role": "user", "content": "读"}]

    result = agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "内容"},
        check=lambda name, arguments: False,
    )

    assert result == "那我换个说法"
    assert len(chat.requests) == 2


def test_check_receives_tool_name_and_parsed_arguments():
    seen = []

    def check(name, arguments):
        seen.append((name, arguments))
        return True

    chat = FakeChat(
        make_turn("", [tool_call("read_file", '{"path": "a.txt"}')]),
        make_turn("好的"),
    )
    messages = [{"role": "user", "content": "读"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
        check=check,
    )

    assert seen == [("read_file", {"path": "a.txt"})]


def test_unknown_tool_never_reaches_permission():
    def check(name, arguments):
        raise AssertionError("未知工具不应进入权限校验")

    chat = FakeChat(make_turn("", [tool_call("nope")]), make_turn("好的"))
    messages = [{"role": "user", "content": "x"}]

    agent_loop(messages, config=CONFIG, chat=chat, registry={}, check=check)

    assert messages[2]["content"] == "未知工具：nope"


def test_unparsable_arguments_never_reach_permission():
    def check(name, arguments):
        raise AssertionError("JSON 解析失败不应进入权限校验")

    chat = FakeChat(
        make_turn("", [tool_call("read_file", "{坏 json")]), make_turn("好的")
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
        check=check,
    )

    assert "不是合法 JSON" in messages[2]["content"]


def test_non_object_arguments_are_rejected_before_permission():
    def check(name, arguments):
        raise AssertionError("非对象参数不应进入权限校验")

    chat = FakeChat(
        make_turn("", [tool_call("read_file", "[1, 2]")]), make_turn("好的")
    )
    messages = [{"role": "user", "content": "x"}]

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda a: "x"},
        check=check,
    )

    assert "JSON 对象" in messages[2]["content"]


def test_denials_still_count_towards_the_round_limit():
    chat = FakeChat(*[make_turn("", [tool_call("read_file")]) for _ in range(3)])
    messages = [{"role": "user", "content": "读"}]

    with pytest.raises(RoundLimitExceeded):
        agent_loop(
            messages,
            config=CONFIG,
            chat=chat,
            registry={"read_file": lambda a: "内容"},
            check=lambda name, arguments: False,
            max_rounds=3,
        )
