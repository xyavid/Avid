"""循环 × 会话：``on_message`` 观察点把每条结算消息落成条目。

这是"会话真的接在运行时上"的端到端证据：一条消息一次提交，续接时读回来，
触发消息按**用户写的那句话**落库（注入的上下文进系统提示词，不写进用户消息），
写入失败则整轮中止。
"""

from __future__ import annotations

import itertools

import pytest

from avid.agent.context import TAIL_HEADER
from avid.agent.hooks import BLOCK
from avid.agent.loop import agent_loop
from avid.agent.state import RunState
from avid.providers.client import Turn, Usage
from avid.providers.config import Config
from avid.providers.transcript import Transcript
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


def tool_call(name="read_file", arguments=None, call_id="call_1"):
    from support import PLACEHOLDER_ARGS

    if arguments is None:
        arguments = PLACEHOLDER_ARGS.get(name, "{}")
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

    def visible(request):
        """发给模型的历史（tail 块每轮重渲染且不落库，不参与这条断言）。"""
        return [
            message["content"]
            for message in request["messages"]
            if not str(message.get("content", "")).startswith(TAIL_HEADER)
        ]

    assert visible(chat.requests[0]) == [
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


def test_trigger_message_is_recorded_verbatim(hook_registry, session):
    """落库的是**用户写的那句话**：注入的上下文进系统提示词，不改写用户消息。

    以前注入被拼在 user content 前面，于是界面把内核写的环境信息当成用户输入显示，
    每次运行的临时环境信息还会在历史里越积越多。
    """

    def inject(context):
        context.setdefault("injected", []).append("[环境] 测试注入")

    hook_registry.register("UserPromptSubmit", inject)
    recorder = SessionRecorder(session)
    messages = [{"role": "user", "content": "原始问题"}]
    chat = FakeChat(make_turn("答"))

    agent_loop(messages, config=CONFIG, chat=chat, on_message=recorder.on_message)

    stored = messages_for_branch(session)
    assert [message["content"] for message in stored] == ["原始问题", "答"]
    assert stored == messages
    # 注入没有丢：它在这次运行的系统提示词里（每轮重建，因此不落库）
    assert "[环境] 测试注入" in chat.requests[0]["system"]


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


def test_a_summarized_history_is_not_summarized_again_next_run(hook_registry, session, tmp_path):
    """诊断 C2 端到端：④ 的摘要经投影成为下次运行的历史——摘要调用只花一次。

    修复前：splice 只改内存，第二个运行从全量历史重新投影，字符再度超限，
    ④ 再花一次摘要调用（每次运行一遍）。
    """

    from avid.agent.context import ContextBudget
    from avid.agent.run import Run
    from avid.agent.spec import RunSpec

    summarize_sizes: list[int] = []

    class Summarizer:
        def __call__(self, config, messages, **kwargs):
            summarize_sizes.append(len(messages))
            return make_turn("[历史摘要] 之前的要点")

    summarizer = Summarizer()
    recorder = SessionRecorder(session)
    # 摘要消息自带 ~160 字符样板（完整记录路径），预算须与触发消息量级分开
    budget = ContextBudget(context_chars=3000, from_window=False)

    def spec_for(chat):
        return RunSpec.resolve(
            config=CONFIG,
            chat=chat,
            summarize=summarizer,
            budget=budget,
        )

    # 运行一：触发消息就超预算 → ④ 摘要替换历史，游标落会话值
    run1 = Run(
        [{"role": "user", "content": "x" * 5000}],
        spec_for(FakeChat(make_turn("干完了一"))),
        state=RunState.for_run(workspace_root=str(tmp_path)),
        on_message=recorder.on_message,
        on_compaction=recorder.record_compaction,
    )
    assert run1.run().text == "干完了一"
    assert len(summarize_sizes) == 1

    # 投影即摘要形态
    projected = messages_for_branch(session, recorder.branch)
    assert projected[0]["content"].startswith("[历史摘要]")

    # 运行二：从摘要形态续接，字符不再超预算 → 摘要调用不重复
    history = messages_for_branch(session, recorder.branch)
    run2 = Run(
        [*history, {"role": "user", "content": "继续"}],
        spec_for(FakeChat(make_turn("接着干完二"))),
        state=RunState.for_run(workspace_root=str(tmp_path)),
        on_message=recorder.on_message,
        on_compaction=recorder.record_compaction,
    )
    assert run2.run().text == "接着干完二"
    assert len(summarize_sizes) == 1  # 修复前这里会变成 2

