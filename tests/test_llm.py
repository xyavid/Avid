import copy
import json
from importlib import import_module

import httpx
import pytest

from avid.providers.client import (
    LLMError,
    PromptTooLongError,
    StreamState,
    ask,
    build_payload,
    build_request,
    delta_reasoning,
    iter_sse_events,
    merge_stream_chunk,
    parse_reply,
    parse_turn,
    stream_completion,
)
from avid.providers.config import Config

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


def test_cache_counters_are_normalized_on_both_paths():
    """Both paths normalise usage the same way: prompt_tokens_details.cached_tokens must be
    read whether the reply arrives whole (parse_turn) or streamed (StreamState.to_turn).
    """
    body = {
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "total_tokens": 105,
            "prompt_tokens_details": {"cached_tokens": 64},
        },
    }
    direct = parse_turn(body)
    assert direct.usage.cache_read_tokens == 64
    assert direct.usage.cache_write_tokens is None

    # Streamed: the last frame carries usage only (empty choices is legal)
    streamed = merge_stream_chunk(StreamState(), {**body, "choices": []}).to_turn()
    assert streamed.usage == direct.usage


def test_deepseek_style_cache_field_is_recognized_at_the_client_seam():
    """OpenAI-compatible endpoints differ: DeepSeek uses prompt_cache_hit_tokens."""
    turn = parse_turn(
        {
            "model": "deepseek-chat",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 7,
                "total_tokens": 207,
                "prompt_cache_hit_tokens": 180,
            },
        }
    )
    assert turn.usage.cache_read_tokens == 180


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


def test_server_error_with_overflow_wording_is_not_treated_as_overflow(monkeypatch):
    """Overflow wording with the wrong status is not an overflow: a server fault must not
    look recoverable."""
    # 500 is retryable at the transport layer: drop the wait, assert convergence to LLMError
    monkeypatch.setattr("avid.providers.transport._sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"error": {"message": "context length overflow"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            ask(CONFIG, "你好", client=client)

    assert not isinstance(exc.value, PromptTooLongError)


def test_a_non_json_body_becomes_an_llm_error():
    """A gateway HTML error page must surface as LLMError, not JSONDecodeError, or svc would
    file it as internal instead of llm_error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>bad gateway</html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as exc:
            ask(CONFIG, "你好", client=client)

    assert not isinstance(exc.value, PromptTooLongError)
    assert "不是合法 JSON" in str(exc.value)


# ---- streaming ----

# Two wire forms of one reply: whole JSON vs SSE frames. The tool_call fragments
# interleave out of order, so merging by index differs from concatenating by arrival order.
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
    """Frames to SSE text, with a comment heartbeat and a trailing [DONE]."""
    blocks = [": ping", ""]
    for frame in frames:
        blocks += ["event: message", "data: " + json.dumps(frame, ensure_ascii=False), ""]
    blocks += ["data: [DONE]", ""]
    return "\n".join(blocks) + "\n"


def fold(frames) -> StreamState:
    """Frames to accumulator state, encoded to SSE text first so the two pure functions
    take the same path as stream_completion."""
    state = StreamState()
    for chunk in iter_sse_events(sse_payload(frames).splitlines()):
        state = merge_stream_chunk(state, chunk)
    return state


def test_stream_and_non_stream_turns_are_field_equal():
    """One mocked SSE stream and its non-stream JSON must produce field-equal Turns."""
    from_stream = fold(STREAM_FRAMES).to_turn()
    from_json = parse_turn(NON_STREAM_TURN)

    assert from_stream == from_json
    assert from_stream.usage.total_tokens == 46
    assert from_stream.finish_reason == "tool_calls"


def test_null_content_is_normalised_the_same_way_in_both_paths():
    """Null content with tool_calls must normalise to "" in both paths, or a raw None reaches
    the next request through the transcript."""
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
    """A content-parts array is joined into text; a list must never land in Turn.text."""
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
    """Interleaved fragments merge by index; concatenating by arrival order would mix them."""
    state = fold(STREAM_FRAMES)
    arguments = [call["function"]["arguments"] for call in state.tool_calls]

    assert arguments == ['{"path":"a.py"}', '{"command":"ls -la"}']
    # The merged arguments must be valid JSON
    assert [json.loads(item) for item in arguments] == [
        {"path": "a.py"},
        {"command": "ls -la"},
    ]
    # The merged call drops the streaming index field to stay field-equal with parse_turn
    assert all("index" not in call for call in state.tool_calls)


def test_merge_stream_chunk_is_pure():
    """merge must not mutate its input state, or a replay or retry would double the text."""
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


REASONING_NON_STREAM = {
    "model": "test-model",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "有两个文件。",
                "reasoning_content": "先看目录。然后读文件。",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
}


REASONING_STREAM_FRAMES = [
    _frame({"reasoning_content": "先看目录。"}),
    _frame({"reasoning_content": "然后读文件。"}),
    _frame({"content": "有两个文件。"}, finish="stop"),
    {"model": "test-model", "choices": [], "usage": REASONING_NON_STREAM["usage"]},
]


def test_reasoning_gets_its_own_field_and_stays_out_of_the_text():
    """Reasoning accumulates in its own field: not the body, and never written back into
    the next round's messages.
    """
    turn = fold(REASONING_STREAM_FRAMES).to_turn()

    assert turn.reasoning == "先看目录。然后读文件。"
    assert turn.text == "有两个文件。"
    assert turn.message == {"role": "assistant", "content": "有两个文件。"}


def test_reasoning_is_parsed_the_same_way_on_both_paths():
    """With reasoning too, stream and non-stream replies stay field-equal."""
    assert fold(REASONING_STREAM_FRAMES).to_turn() == parse_turn(REASONING_NON_STREAM)


def test_delta_reasoning_reads_only_the_thinking_piece():
    """Three vendor spellings give one result; body text and tool arguments are not reasoning."""
    assert delta_reasoning(_frame({"reasoning": "嗯"})) == "嗯"
    assert delta_reasoning(_frame({"reasoning_content": "嗯"})) == "嗯"
    assert delta_reasoning(_frame({"reasoning_details": [{"text": "嗯"}]})) == "嗯"
    assert delta_reasoning(_frame({"content": "正文"})) == ""
    assert delta_reasoning({"model": "m", "choices": [], "usage": REASONING_NON_STREAM["usage"]}) == ""


def test_stream_completion_reports_reasoning_on_its_own_callback():
    """Reasoning and body travel on two callbacks so the front end can show them apart."""
    thought: list[str] = []
    body: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_payload(REASONING_STREAM_FRAMES).encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        turn = stream_completion(
            CONFIG,
            [{"role": "user", "content": "看两个文件"}],
            on_delta=body.append,
            on_reasoning=thought.append,
            client=client,
        )

    assert thought == ["先看目录。", "然后读文件。"]
    assert body == ["有两个文件。"]
    assert turn.reasoning == "先看目录。然后读文件。"


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

    # on_delta gets body fragments only: tool-argument fragments are never user-visible
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
    """A new Client per call re-handshakes and adds up across rounds, so assert one construction
    (same instance twice) without a real request."""
    import httpx as httpx_module

    from avid.providers import client as client_module

    created: list[int] = []
    real_client = httpx_module.Client

    def counting_client(*args, **kwargs):
        created.append(1)
        return real_client(*args, **kwargs)

    # The singleton lives in transport: patch its Client and state (client re-exports it)
    transport_module = import_module("avid.providers.transport")
    monkeypatch.setattr(transport_module.httpx, "Client", counting_client)
    monkeypatch.setattr(transport_module, "_CLIENT", None)

    first = client_module.shared_client()
    second = client_module.shared_client()

    assert first is second
    assert created == [1], f"构造了 {len(created)} 个 Client"


def test_connect_timeout_is_tighter_than_the_read_timeout():
    """An unreachable endpoint must not wait it out: connect stays tighter than read."""
    from avid.providers.client import CONNECT_TIMEOUT_SECONDS, TIMEOUT_SECONDS, _timeout

    timeout = _timeout()
    assert timeout.connect == CONNECT_TIMEOUT_SECONDS
    assert timeout.read == TIMEOUT_SECONDS
    assert CONNECT_TIMEOUT_SECONDS < TIMEOUT_SECONDS


# ---- output budget: no default cap ----


def test_request_sends_no_max_tokens_by_default():
    """No output cap by default: a fixed max_tokens is spent on reasoning and the body comes back
    empty (finish_reason=length), so the cap is the provider's to decide."""
    request = build_request(CONFIG, [{"role": "user", "content": "hi"}])

    assert "max_tokens" not in request


def test_request_still_sends_max_tokens_when_the_caller_caps_it():
    request = build_request(
        CONFIG, [{"role": "user", "content": "hi"}], max_tokens=1234
    )

    assert request["max_tokens"] == 1234
