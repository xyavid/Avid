"""CoT（思考）按路由回传的失败清单与不变量。

可能怎么坏（逐条对应下面的用例）：① 该带的不带（同路由 + 工具链，协议要求连续）；② 不该带的带
（普通历史 / 跨路由，会把上一轮 CoT 灌进新问题）；③ 带成了正文（思考进了 content）；④ 元数据漏上
线路（消息上多挂的键原样发出去）；⑤ 归档丢了（日志往返后记录不在）；⑥ 索引把思考当正文搜出来；
⑦ 出错时不说人话（端点拒绝回传时给不出可执行提示）。
"""

from __future__ import annotations

import time

from support import bound_workspace, make_turn, wait_terminal

from avid.index.extract import entry_text
from avid.providers import anthropic, openai_compat, reasoning
from avid.providers.config import Config
from avid.providers.protocol import assistant_message, http_error
from avid.services import Services
from avid.session.projection import entries_to_messages
from avid.session.types import MESSAGE_ENTRY, Entry

MODEL = "deepseek/deepseek-v4.1-flash"
THINKING = "先看 pyproject，再决定读哪个文件。"


def make_config(*, model: str = MODEL, family: str = "openai", provider_id: str = "command") -> Config:
    return Config(
        api_key="sk-test",
        base_url="https://api.example/v1",
        model=model,
        provider=family,
        provider_id=provider_id,
    )


def assistant_turn(
    *,
    config: Config | None = None,
    text: str = "",
    with_tools: bool = True,
    thinking: str = THINKING,
    signature: str | None = None,
) -> dict:
    """One assistant message as the run loop would append it:正文 + 工具调用 + 思考记录。"""
    config = config or make_config()
    calls = (
        [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
        if with_tools
        else []
    )
    message = assistant_message(text, calls)
    blocks: list[dict] = []
    if signature is None:
        blocks.append({"type": "text", "text": thinking})
    else:
        blocks.append({"type": "thinking", "thinking": thinking, "signature": signature})
    reasoning.attach(message, blocks, route=reasoning.route_of(config))
    return message


def wire_messages(config: Config, messages: list[dict]) -> list[dict]:
    request = openai_compat.build_request(config, messages, system=None, tools=None)
    return request["messages"]


# ---------------- ① 该带就带：同路由 + 工具链 ----------------


def test_same_route_tool_chain_replays_reasoning_content():
    config = make_config()
    messages = [
        {"role": "user", "content": "看一下项目"},
        assistant_turn(config=config),
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    assistant = wire_messages(config, messages)[1]

    assert assistant["reasoning_content"] == THINKING
    # 正文没被污染，工具调用也在。
    assert assistant["content"] == ""
    assert assistant["tool_calls"][0]["id"] == "c1"


def test_every_stored_field_but_the_protocol_ones_is_ignored():
    config = make_config()
    message = assistant_turn(config=config)
    message["some_future_key"] = {"anything": True}
    messages = [{"role": "user", "content": "看"}, message, {"role": "tool", "tool_call_id": "c1", "content": "ok"}]

    assistant = wire_messages(config, messages)[1]

    # 对外发送是白名单：多挂的键（含我们自己的记录字段）都不上线路。
    assert set(assistant) <= {"role", "content", "tool_calls", "reasoning_content"}
    assert "some_future_key" not in assistant
    assert reasoning.RECORD_KEY not in assistant


# ---------------- ② 不该带就不带 ----------------


def test_reasoning_is_not_replayed_without_a_tool_result():
    config = make_config()
    messages = [
        {"role": "user", "content": "看一下项目"},
        assistant_turn(config=config, text="看完了", with_tools=False),
        {"role": "user", "content": "那再改一下"},
    ]

    for message in wire_messages(config, messages):
        assert "reasoning_content" not in message
        assert THINKING not in str(message.get("content", ""))


def test_a_finished_turn_chain_is_not_replayed_again():
    """上一轮已经收尾的 think→tool→result 不该在下一轮的请求里再发一遍（连续性要求已经过去）。"""
    config = make_config()
    messages = [
        {"role": "user", "content": "第一轮"},
        assistant_turn(config=config, thinking="第一轮的思考"),
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
        assistant_turn(config=config, text="第一轮答案", with_tools=False),
        {"role": "user", "content": "第二轮"},
        assistant_turn(config=config, thinking="第二轮的思考"),
        {"role": "tool", "tool_call_id": "c1", "content": "结果2"},
    ]

    carriers = [m for m in wire_messages(config, messages) if m.get("reasoning_content")]

    assert len(carriers) == 1
    assert carriers[0]["reasoning_content"] == "第二轮的思考"


def test_cross_route_reasoning_is_dropped():
    other = make_config(model="gpt-5")
    config = make_config()
    messages = [
        {"role": "user", "content": "看一下项目"},
        assistant_turn(config=other),  # 上一轮是别的模型想的
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    assert "reasoning_content" not in wire_messages(config, messages)[1]


def test_other_protocol_reasoning_is_dropped():
    # 记录来自 anthropic 的签名块，目标却是 openai 兼容端点：不带（且绝不降级成正文）。
    signed = assistant_turn(config=make_config(family="anthropic"), signature="sig-1")
    config = make_config()
    messages = [
        {"role": "user", "content": "看一下项目"},
        signed,
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    assistant = wire_messages(config, messages)[1]
    assert "reasoning_content" not in assistant
    assert "reasoning" not in assistant
    assert THINKING not in str(assistant.get("content", ""))


# ---------------- ③ anthropic：块 + 签名 ----------------


def test_anthropic_replays_thinking_blocks_before_text_and_tool_use():
    config = make_config(family="anthropic")
    messages = [
        {"role": "user", "content": "看一下项目"},
        assistant_turn(config=config, text="我读一下", signature="sig-abc"),
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    built = anthropic.build_messages(messages, config)
    blocks = built[1]["content"]

    assert blocks[0] == {"type": "thinking", "thinking": THINKING, "signature": "sig-abc"}
    assert blocks[1] == {"type": "text", "text": "我读一下"}
    assert blocks[2]["type"] == "tool_use"


def test_anthropic_drops_blocks_without_a_signature():
    config = make_config(family="anthropic")
    messages = [
        {"role": "user", "content": "看一下项目"},
        assistant_turn(config=config, signature=None),  # 只有文本、没有签名
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    blocks = anthropic.build_messages(messages, config)[1]["content"]

    assert all(block["type"] != "thinking" for block in blocks)


def test_anthropic_parse_keeps_the_signature():
    data = {
        "model": "claude-sonnet-4",
        "content": [
            {"type": "thinking", "thinking": THINKING, "signature": "sig-xyz"},
            {"type": "text", "text": "好了"},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "stop_reason": "end_turn",
    }

    turn = anthropic.parse_turn(data)

    # 解析层保留整块（含签名），记录由调用边界按路由挂上（下一个用例）。
    assert turn.reasoning_blocks[0]["signature"] == "sig-xyz"
    assert turn.reasoning == THINKING
    assert turn.message["content"] == "好了"
    assert reasoning.record_of(turn.message) is None  # 记录由边界挂，解析层不认识路由


def test_anthropic_stream_keeps_the_signature():
    # 流式那一路也必须把 signature 收进记录（不然回传时整块被丢）。
    frames = [
        {"type": "message_start", "message": {"model": "claude-sonnet-4", "usage": {"input_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": THINKING}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-s"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    acc = anthropic._Accumulator()
    for frame in frames:
        acc.feed(frame)
    turn = acc.to_turn()

    assert turn.reasoning_blocks[0] == {
        "type": "thinking",
        "thinking": THINKING,
        "signature": "sig-s",
    }


# ---------------- ④ 归档：落库、往返、索引 ----------------


def test_record_survives_the_session_projection():
    message = assistant_turn()
    entry = Entry(
        id="e1", parent_id=None, seq=1, timestamp=1, type=MESSAGE_ENTRY, message=message
    )

    projected = entries_to_messages([entry])[0]

    record = reasoning.record_of(projected)
    assert record is not None and record["blocks"][0]["text"] == THINKING
    assert record["route"] == "command/deepseek/deepseek-v4.1-flash"
    assert record["protocol"] == "openai"


def test_index_does_not_pick_up_reasoning():
    role, text = entry_text(MESSAGE_ENTRY, assistant_turn())

    assert role == "assistant"
    assert THINKING not in text


# ---------------- ⑤ 失败路径：说人话 ----------------


def test_rejected_replay_gets_a_repair_hint():
    error = http_error(400, "unexpected field reasoning_content", sent_reasoning_replay=True)

    assert "思考" in str(error)


def test_the_boundary_attaches_the_record_with_the_route(monkeypatch):
    # chat() 是唯一知道「这一轮走的是哪条路由」的地方，记录在这里挂上。
    config = make_config()
    payload = {
        "model": MODEL,
        "choices": [
            {
                "message": {"role": "assistant", "content": "好了", "reasoning_content": THINKING},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    monkeypatch.setattr(openai_compat, "post", lambda *a, **k: payload)

    turn = openai_compat.chat(config, [{"role": "user", "content": "看一下"}])

    record = reasoning.record_of(turn.message)
    assert record == {
        "route": "command/deepseek/deepseek-v4.1-flash",
        "protocol": "openai",
        "blocks": [{"type": "text", "text": THINKING}],
    }


def test_the_rounds_thinking_duration_lands_in_the_record(sandbox, monkeypatch):
    """思考时长从 delta 的真实到达时刻量、随记录落库：重开卡片写「思考 · 2.4s」而不是「思考完成」。"""
    from avid.providers import reasoning as reasoning_module
    from avid.services import runs as runs_module

    def fake_stream(config, messages, *, on_reasoning=None, **kwargs):
        # 生产路径：服务把 on_reasoning 接到 emit_delta，时长就是在那里量的。
        assert on_reasoning is not None
        on_reasoning("先看一眼")
        time.sleep(0.05)
        on_reasoning("再想一下")
        turn = make_turn("看完了")
        reasoning_module.attach(
            turn.message,
            [{"type": "text", "text": "先看一眼再想一下"}],
            route=reasoning_module.route_of(config),
        )
        return turn

    monkeypatch.setattr(runs_module, "stream_completion", fake_stream)
    services = Services(root=sandbox / ".avid" / "sessions")
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]

    record = services.runs.start(session_id, "看一眼")
    assert wait_terminal(record)

    page = services.sessions.entries(session_id, branch="main", order="asc")
    records = []
    for entry in page["entries"]:
        found = ((entry or {}).get("message") or {}).get("reasoning")
        if isinstance(found, dict):
            records.append(found)
    assert len(records) == 1
    assert records[0]["blocks"][0]["text"] == "先看一眼再想一下"
    assert 40 <= records[0]["ms"] <= 5000
