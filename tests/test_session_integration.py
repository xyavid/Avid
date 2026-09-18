"""循环 × 会话：``on_message`` 观察点把每条结算消息落成条目。

这是"会话真的接在运行时上"的端到端证据：一条消息一次提交，续接时读回来，
注入过的触发消息按注入后的版本落库，写入失败则整轮中止。
"""

from __future__ import annotations

import itertools

import pytest

from avid.ai.client import Turn, Usage
from avid.ai.config import Config
from avid.ai.transcript import Transcript
from avid.runtime.hooks import BLOCK
from avid.runtime.loop import agent_loop
from avid.session import (
    MemorySessionRepo,
    SessionClosedError,
    SessionRecorder,
    UuidV7Generator,
    messages_for_branch,
)

CONFIG = Config(api_key="k", base_url="http://localhost", model="m")


class FakeChat:
    """按顺序返回预设轮次，并记录每轮收到的参数（与 test_agent.py 同形）。"""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.turns[len(self.requests) - 1]


def make_turn(text="", tool_calls=(), finish_reason="stop"):
    return Turn(
        message={"role": "assistant", "content": text, **({"tool_calls": list(tool_calls)} if tool_calls else {})},
        text=text,
        tool_calls=list(tool_calls),
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="m",
        finish_reason=finish_reason,
    )


def tool_call(name="read_file", arguments="{}", call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.fixture
def session():
    counter = itertools.count(1_700_000_000_000, 1_000)
    tick = lambda: next(counter)  # noqa: E731
    repo = MemorySessionRepo(now=tick, id_generator=UuidV7Generator(tick))
    opened = repo.create(id="s")
    yield opened
    if not opened.closed:
        opened.close()
    repo.close()


def test_every_settled_message_is_persisted_in_order(hook_registry, session):
    recorder = SessionRecorder(session)
    messages = [{"role": "user", "content": "你好"}]
    chat = FakeChat(make_turn("答"))

    assert agent_loop(messages, config=CONFIG, chat=chat, on_message=recorder.on_message) == "答"

    assert recorder.count == 2
    assert messages_for_branch(session) == messages


def test_tool_round_trip_is_persisted(hook_registry, session):
    recorder = SessionRecorder(session)
    messages = [{"role": "user", "content": "读文件"}]
    chat = FakeChat(make_turn("", [tool_call()]), make_turn("读完"))

    agent_loop(
        messages,
        config=CONFIG,
        chat=chat,
        registry={"read_file": lambda args, **kwargs: "文件内容"},
        on_message=recorder.on_message,
    )

    stored = messages_for_branch(session)
    assert [message["role"] for message in stored] == ["user", "assistant", "tool", "assistant"]
    assert stored[2]["content"] == "文件内容"
    assert Transcript(stored).validate() == []


def test_second_run_continues_from_the_session(hook_registry, session):
    recorder = SessionRecorder(session)
    first = [{"role": "user", "content": "第一问"}]
    agent_loop(first, config=CONFIG, chat=FakeChat(make_turn("第一答")), on_message=recorder.on_message)

    history = messages_for_branch(session)
    assert [message["content"] for message in history] == ["第一问", "第一答"]

    second = [*history, {"role": "user", "content": "第二问"}]
    chat = FakeChat(make_turn("第二答"))
    agent_loop(second, config=CONFIG, chat=chat, on_message=recorder.on_message)

    assert [message["content"] for message in chat.requests[0]["messages"]] == [
        "第一问",
        "第一答",
        "第二问",
    ]
    assert [message["role"] for message in messages_for_branch(session)] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_trigger_message_is_recorded_after_injection(hook_registry, session):

    def inject(context):
        context.setdefault("injected", []).append("[环境] 测试注入")

    hook_registry.register("UserPromptSubmit", inject)
    recorder = SessionRecorder(session)
    messages = [{"role": "user", "content": "原始问题"}]

    agent_loop(messages, config=CONFIG, chat=FakeChat(make_turn("答")), on_message=recorder.on_message)

    stored = messages_for_branch(session)
    assert stored[0]["content"].startswith("[环境] 测试注入")
    assert stored[0]["content"].endswith("原始问题")
    assert stored == messages


def test_stop_nudge_is_persisted(hook_registry, session):

    def stop_hook(context):
        if context["rounds"] == 1:
            context["nudge"] = "还有一步"
            return BLOCK
        return None

    hook_registry.register("Stop", stop_hook)
    recorder = SessionRecorder(session)
    messages = [{"role": "user", "content": "问题"}]
    chat = FakeChat(make_turn("第一答"), make_turn("第二答"))

    agent_loop(messages, config=CONFIG, chat=chat, on_message=recorder.on_message)

    assert [message["content"] for message in messages_for_branch(session)] == [
        "问题",
        "第一答",
        "还有一步",
        "第二答",
    ]


def test_without_recorder_the_session_stays_empty(hook_registry, session):
    messages = [{"role": "user", "content": "你好"}]
    agent_loop(messages, config=CONFIG, chat=FakeChat(make_turn("答")))
    assert session.get_stats().message_count == 0
    assert session.find_entries() == []


def test_recording_failure_stops_the_run_before_calling_the_model(hook_registry, session):
    recorder = SessionRecorder(session)
    session.close()
    chat = FakeChat(make_turn("答"))
    messages = [{"role": "user", "content": "你好"}]

    with pytest.raises(SessionClosedError):
        agent_loop(messages, config=CONFIG, chat=chat, on_message=recorder.on_message)
    assert chat.requests == []


def test_history_is_not_re_recorded(hook_registry, session):
    recorder = SessionRecorder(session)
    recorder.on_message({"role": "user", "content": "旧的"})
    recorder.on_message({"role": "assistant", "content": "旧的答"})

    messages = [*messages_for_branch(session), {"role": "user", "content": "新的"}]
    agent_loop(messages, config=CONFIG, chat=FakeChat(make_turn("新答")), on_message=recorder.on_message)

    assert [message["content"] for message in messages_for_branch(session)] == [
        "旧的",
        "旧的答",
        "新的",
        "新答",
    ]
