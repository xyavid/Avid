import copy
import json

import httpx
import pytest

from avid.ai.client import (
    LLMError,
    PromptTooLongError,
    StreamState,
    ask,
    build_payload,
    iter_sse_events,
    merge_stream_chunk,
    parse_reply,
    parse_turn,
    stream_completion,
)
from avid.ai.config import Config

CONFIG = Config(api_key="test-key", base_url="https://api.test/v1", model="test-model")


def test_payload_carries_model_and_messages():
    assert build_payload(CONFIG, "你好") == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "你好"}],
    }


def test_ask_sends_auth_header_and_reads_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": "你好，我是 Avid。"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        reply = ask(CONFIG, "你好", client=client)

    assert reply.text == "你好，我是 Avid。"
    assert reply.usage.total_tokens == 18
    assert reply.model == "test-model"


def test_http_error_reports_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            ask(CONFIG, "你好", client=client)

    assert "401" in str(exc.value)
    assert "invalid api key" in str(exc.value)


def test_response_without_content_is_rejected():
    with pytest.raises(LLMError):
        parse_reply({"choices": []})


def test_missing_usage_falls_back_to_zero():
    reply = parse_reply({"choices": [{"message": {"content": "hi"}}]})

    assert reply.usage.total_tokens == 0


# ---------- prompt_too_long ----------


@pytest.mark.parametrize(
    "message",
    [
        "prompt is too long",
        "context_length_exceeded",
        "Request too large for this model",
        "input is too long, please reduce the length",
        "This model's maximum context length is 8192 tokens",
    ],
)
def test_prompt_too_long_signatures(message):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": message}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PromptTooLongError):
            ask(CONFIG, "你好", client=client)


def test_overflow_is_also_recognised_on_413():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, text="request too large")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PromptTooLongError):
            ask(CONFIG, "你好", client=client)


def test_other_400s_stay_plain_llm_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid model name"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            ask(CONFIG, "你好", client=client)

    assert not isinstance(exc.value, PromptTooLongError)


def test_server_error_with_overflow_wording_is_not_treated_as_overflow():
    """状态码不对就不算上下文超限，避免把服务端故障当成可恢复的。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"error": {"message": "context length overflow"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            ask(CONFIG, "你好", client=client)

    assert not isinstance(exc.value, PromptTooLongError)


def test_a_non_json_body_becomes_an_llm_error():
    """网关返回 HTML 错误页时必须是 LLMError，不能漏出 JSONDecodeError。

    否则调用方按"模型层失败"分类的路径接不住它：svc 会把它归成 internal
    而不是 llm_error，前端拿到的错误分类就是错的。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>bad gateway</html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            ask(CONFIG, "你好", client=client)

    assert not isinstance(exc.value, PromptTooLongError)
    assert "不是合法 JSON" in str(exc.value)


# ---------- 流式（F3） ----------

# 同一份内容的两种线格式：非流式 JSON 与流式 SSE 分片。
# tool_calls 的分片**故意交错**（0 的第一片、1 的第一片、0 的第二片、1 的第二片）：
# 「按 index 归并」与「按到达顺序拼接」在这种输入上结果不同，后者是错的。
NON_STREAM_TURN = {
    "model": "test-model",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "我先看两个文件。",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command":"ls -la"}'},
                    },
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
}


def _frame(delta, finish=None):
    return {"model": "test-model", "choices": [{"delta": delta, "finish_reason": finish}]}


def _call(index, **fields):
    return {"index": index, **fields}


STREAM_FRAMES = [
    _frame({"role": "assistant", "content": ""}),
    _frame({"content": "我先看"}),
    _frame({"content": "两个文件。"}),
    _frame(
        {
            "tool_calls": [
                _call(0, id="call_1", type="function", function={"name": "read_file", "arguments": ""})
            ]
        }
    ),
    _frame(
        {
            "tool_calls": [
                _call(1, id="call_2", type="function", function={"name": "bash", "arguments": ""})
            ]
        }
    ),
    _frame({"tool_calls": [_call(0, function={"arguments": '{"path":'})]}),
    _frame({"tool_calls": [_call(1, function={"arguments": '{"command":'})]}),
    _frame({"tool_calls": [_call(0, function={"arguments": '"a.py"}'})]}),
    _frame({"tool_calls": [_call(1, function={"arguments": '"ls -la"}'})]}),
    _frame({}, finish="tool_calls"),
    {"model": "test-model", "choices": [], "usage": NON_STREAM_TURN["usage"]},
]


def sse_payload(frames) -> str:
    """帧列表 → SSE 文本，带一行注释心跳与结尾的 `[DONE]`。"""
    blocks = [": ping", ""]
    for frame in frames:
        blocks += ["event: message", "data: " + json.dumps(frame, ensure_ascii=False), ""]
    blocks += ["data: [DONE]", ""]
    return "\n".join(blocks) + "\n"


def fold(frames) -> StreamState:
    """帧 → 累加器状态。先编成 SSE 文本再解析，于是 `iter_sse_events` 与
    `merge_stream_chunk` 两个纯函数走的是与 `stream_completion` 完全相同的路径。"""
    state = StreamState()
    for chunk in iter_sse_events(sse_payload(frames).splitlines()):
        state = merge_stream_chunk(state, chunk)
    return state


def test_stream_and_non_stream_turns_are_field_equal():
    """B9：同一段 mock SSE 与同一份非流式 JSON 必须产出逐字段相等的 Turn。"""
    from_stream = fold(STREAM_FRAMES).to_turn()
    from_json = parse_turn(NON_STREAM_TURN)

    assert from_stream == from_json
    assert from_stream.usage.total_tokens == 46
    assert from_stream.finish_reason == "tool_calls"


def test_null_content_is_normalised_the_same_way_in_both_paths():
    """带 tool_calls 时 content 为 null 是常见形状：两条路径必须给它同一个答案。

    修之前非流式把 `None` 原样写进 message（流式写 `""`）——那份 None 会随
    transcript 进入下一轮请求，"同形"在最常见的一种响应上就不成立。
    """
    data = {
        "model": "test-model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    from_json = parse_turn(data)
    from_stream = fold(
        [
            _frame(
                {
                    "tool_calls": [
                        _call(
                            0,
                            id="call_1",
                            type="function",
                            function={"name": "read_file", "arguments": '{"path":"a.py"}'},
                        )
                    ]
                },
                finish="tool_calls",
            )
        ]
    ).to_turn()

    assert from_json.message == from_stream.message
    assert from_json.message["content"] == ""
    assert from_json.text == ""


def test_content_parts_are_joined_into_text():
    """分片数组形状也归一成正文，而不是把 list 当成 Turn.text。"""
    turn = parse_turn(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "前"},
                            {"type": "text", "text": "后"},
                        ],
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )

    assert turn.text == "前后"
    assert turn.message == {"role": "assistant", "content": "前后"}


def test_interleaved_tool_call_fragments_merge_by_index():
    """分片交错时按 index 归并；按到达顺序拼会把两个调用的参数搅在一起。"""
    state = fold(STREAM_FRAMES)
    arguments = [call["function"]["arguments"] for call in state.tool_calls]

    assert arguments == ['{"path":"a.py"}', '{"command":"ls -la"}']
    # 拼出来的必须真是合法 JSON —— 「拼完再 loads」这条约束的落点。
    assert [json.loads(item) for item in arguments] == [
        {"path": "a.py"},
        {"command": "ls -la"},
    ]
    # 归并产物不带流式的 index 字段：要与 parse_turn 的产物同形（B9 逐字段相等的前提）。
    assert all("index" not in call for call in state.tool_calls)


def test_merge_stream_chunk_is_pure():
    """fold 不得就地改入参：否则重放或重试会累加出双倍文本。"""
    first = merge_stream_chunk(
        StreamState(),
        _frame(
            {
                "tool_calls": [
                    _call(0, id="c", function={"name": "bash", "arguments": '{"a":'})
                ]
            }
        ),
    )
    before = copy.deepcopy(first)

    second = merge_stream_chunk(
        first,
        _frame({"content": "hi", "tool_calls": [_call(0, function={"arguments": "1}"})]}),
    )

    assert first == before, "入参状态被就地改了"
    assert first.tool_calls[0]["function"]["arguments"] == '{"a":'
    assert second.tool_calls[0]["function"]["arguments"] == '{"a":1}'
    assert second.content == "hi"


def test_sse_reader_skips_heartbeat_empty_and_done():
    lines = [
        ": ping",
        "",
        "event: message",
        'data: {"choices":[{"delta":{"content":"a"}}]}',
        "",
        "data:",
        "",
        "data: [DONE]",
        "",
    ]

    assert [chunk["choices"][0]["delta"]["content"] for chunk in iter_sse_events(lines)] == ["a"]


def test_sse_reader_keeps_last_frame_without_trailing_blank_line():
    assert len(list(iter_sse_events(['data: {"choices":[]}']))) == 1


def test_sse_reader_rejects_broken_frame():
    with pytest.raises(LLMError) as exc:
        list(iter_sse_events(["data: {not json}", ""]))

    assert "不是合法 JSON" in str(exc.value)


def test_stream_completion_returns_turn_and_reports_deltas():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["model"] == "test-model"
        return httpx.Response(
            200,
            content=sse_payload(STREAM_FRAMES).encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        turn = stream_completion(
            CONFIG,
            [{"role": "user", "content": "看两个文件"}],
            on_delta=seen.append,
            client=client,
        )

    # 回调只送正文分片：工具参数的片段不该出现在给人看的增量里。
    assert seen == ["我先看", "两个文件。"]
    assert turn == parse_turn(NON_STREAM_TURN)


def test_stream_completion_http_error_carries_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            stream_completion(CONFIG, [{"role": "user", "content": "hi"}], client=client)

    assert "401" in str(exc.value)
    assert "invalid api key" in str(exc.value)


def test_stream_without_usage_frame_falls_back_to_zero():
    turn = fold([_frame({"content": "hi"}, finish="stop")]).to_turn()

    assert turn.text == "hi"
    assert turn.usage.total_tokens == 0
    assert turn.finish_reason == "stop"


def test_the_http_client_is_created_once_and_reused(monkeypatch):
    """每次调用新建 Client 会重新握手：多轮 agent 与 subagent 线性叠加。

    这条断言机制（只构造一次、两次拿到同一个实例），不发真实请求。
    """
    import httpx as httpx_module

    from avid.ai import client as client_module

    created: list[int] = []
    real_client = httpx_module.Client

    def counting_client(*args, **kwargs):
        created.append(1)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "Client", counting_client)
    monkeypatch.setattr(client_module, "_CLIENT", None)

    first = client_module.shared_client()
    second = client_module.shared_client()

    assert first is second
    assert created == [1], f"构造了 {len(created)} 个 Client"


def test_connect_timeout_is_tighter_than_the_read_timeout():
    """端点不可达时不该等满 60 秒；长回答的读超时仍留足。"""
    from avid.ai.client import CONNECT_TIMEOUT_SECONDS, TIMEOUT_SECONDS, _timeout

    timeout = _timeout()
    assert timeout.connect == CONNECT_TIMEOUT_SECONDS
    assert timeout.read == TIMEOUT_SECONDS
    assert CONNECT_TIMEOUT_SECONDS < TIMEOUT_SECONDS
