"""providers/ 三协议与传输层重试的单元测试。

先于实现编写（§6 约定）。覆盖：

* ``transport``：429/5xx/网络错误的重试矩阵（Retry-After、退避序列、耗尽后收敛）；
  流式只在首字节前可重试。
* ``anthropic`` / ``gemini``：请求构造（system 落位、tool_calls↔原生块、tool 结果
  映射）与响应解析（非流式 JSON 与流式 SSE 产出**同形** Turn，B9）。
* ``config``：provider 探测与 AVID_PROVIDER 覆盖。

所有 HTTP 都走 ``httpx.MockTransport``；sleeper 注入为记录函数，测试不真睡。
"""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest

from avid.providers import transport
from avid.providers.client import ask, chat_completion, stream_completion
from avid.providers.config import Config, ConfigError, window_for
from avid.providers.protocol import LLMError, PromptTooLongError
from avid.providers.providers import anthropic, gemini
from avid.providers.transport import RetryPolicy

OPENAI_CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="test-model")
ANTHROPIC_CONFIG = Config(
    api_key="k", base_url="https://api.anthropic.com", model="claude-test", provider="anthropic"
)
GEMINI_CONFIG = Config(
    api_key="k",
    base_url="https://generativelanguage.googleapis.com/v1beta",
    model="gemini-test",
    provider="gemini",
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读一个文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]


def _recording_sleeper():
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, sleeper


def _mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------- transport：重试矩阵 ----------------


class TestRetry:
    def test_500_then_success_retries_with_backoff(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"ok": True}) if calls["n"] > 2 else httpx.Response(500)

        record, sleeper = _recording_sleeper()
        with _mock(handler) as http:
            response = transport.send(
                http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                policy=RetryPolicy(jitter=0.0, sleeper=sleeper),
            )
        assert response.status_code == 200
        assert record == [0.5, 1.0]  # base * 2**attempt

    def test_retry_after_header_overrides_backoff(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] > 1:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(429, headers={"retry-after": "2"})

        record, sleeper = _recording_sleeper()
        with _mock(handler) as http:
            transport.send(
                http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                policy=RetryPolicy(jitter=0.0, sleeper=sleeper),
            )
        assert record == [2.0]

    def test_401_is_returned_without_retry(self):
        record, sleeper = _recording_sleeper()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        with _mock(handler) as http:
            response = transport.send(
                http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                policy=RetryPolicy(sleeper=sleeper),
            )
        assert response.status_code == 401
        assert record == []

    def test_retries_exhausted_returns_last_response(self):
        record, sleeper = _recording_sleeper()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="overloaded")

        with _mock(handler) as http:
            response = transport.send(
                http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                policy=RetryPolicy(attempts=3, jitter=0.0, sleeper=sleeper),
            )
        assert response.status_code == 503
        assert record == [0.5, 1.0]  # 3 次尝试之间只有 2 段等待

    def test_network_error_retries_then_raises_llm_error(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] > 1:
                return httpx.Response(200, json={"ok": True})
            raise httpx.ConnectError("boom", request=request)

        record, sleeper = _recording_sleeper()
        with _mock(handler) as http:
            response = transport.send(
                http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                policy=RetryPolicy(jitter=0.0, sleeper=sleeper),
            )
        assert response.status_code == 200
        assert record == [0.5]

        record, sleeper = _recording_sleeper()

        def always_down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with _mock(always_down) as http:
            with pytest.raises(LLMError, match="请求 https://api.test/v1/x 失败"):
                transport.send(
                    http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                    policy=RetryPolicy(attempts=2, jitter=0.0, sleeper=sleeper),
                )
        assert record == [0.5]

    def test_stream_retry_only_before_first_byte(self):
        """状态在响应头阶段就可判定：503 后重试成功。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] > 1:
                return httpx.Response(200, text='data: {"ok": true}\n\n')
            return httpx.Response(503, text="overloaded")

        record, sleeper = _recording_sleeper()
        with _mock(handler) as http:
            response = transport.send_stream(
                http, "POST", "https://api.test/v1/x", headers={}, json_body={},
                policy=RetryPolicy(jitter=0.0, sleeper=sleeper),
            )
            with contextlib.closing(response):
                assert response.status_code == 200
                body = list(response.iter_lines())
        assert record == [0.5]
        assert body == ['data: {"ok": true}', ""]


# ---------------- config：协议族与窗口表 ----------------


class TestProviderDetection:
    def test_resolved_provider_validates_the_family(self):
        """协议族来自 BYOK 的显式声明；非法值报错而不是猜。"""
        assert Config(api_key="k", base_url="https://x/v1", model="m", provider="anthropic").resolved_provider == "anthropic"
        with pytest.raises(ConfigError, match="provider"):
            _ = Config(api_key="k", base_url="https://x/v1", model="m", provider="palm").resolved_provider

    def test_window_table_knows_newer_prefixes(self):
        assert window_for("claude-sonnet-4-5") == 200_000
        assert window_for("claude-haiku-4-5") == 200_000
        assert window_for("gemini-3-pro") == 1_048_576


# ---------------- Anthropic Messages API ----------------


ANTHROPIC_MESSAGES = [
    {"role": "user", "content": "hi"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "tc1", "content": "file data"},
    {"role": "user", "content": "thanks"},
]


class TestAnthropic:
    def test_request_shape(self):
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["req"] = request
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        with _mock(handler) as http:
            anthropic.chat(
                ANTHROPIC_CONFIG,
                ANTHROPIC_MESSAGES,
                system="sys",
                tools=TOOLS,
                max_tokens=None,
                client=http,
            )

        request = captured["req"]
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "k"
        assert request.headers["anthropic-version"] == "2023-06-01"

        body = json.loads(request.content)
        assert body["model"] == "claude-test"
        assert body["system"] == "sys"
        assert body["max_tokens"] == anthropic.DEFAULT_MAX_TOKENS  # Anthropic 必填
        # tool 消息合并进一条 user 消息的 tool_result 块
        assert body["messages"] == [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tc1", "name": "read_file",
                     "input": {"path": "a.txt"}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "file data"}],
            },
            {"role": "user", "content": "thanks"},
        ]
        assert body["tools"] == [
            {
                "name": "read_file",
                "description": "读一个文件",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]

    def test_turn_shape(self):
        data = {
            "id": "msg_1",
            "model": "claude-test",
            "content": [
                {"type": "thinking", "thinking": "let me look", "signature": "sig"},
                {"type": "text", "text": "Reading it."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "a.txt"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        }

        with _mock(lambda request: httpx.Response(200, json=data)) as http:
            turn = anthropic.chat(
                ANTHROPIC_CONFIG, [{"role": "user", "content": "hi"}], client=http
            )

        assert turn.text == "Reading it."
        assert turn.reasoning == "let me look"
        assert turn.finish_reason == "tool_calls"
        assert turn.model == "claude-test"
        # Anthropic 的 input_tokens 不含缓存部分：prompt 是三段之和（口径见 ai/usage.py）。
        assert turn.usage.prompt_tokens == 15
        assert turn.usage.cache_read_tokens == 3
        assert turn.usage.cache_write_tokens == 2
        assert json.loads(turn.tool_calls[0]["function"]["arguments"]) == {"path": "a.txt"}
        assert turn.message == {
            "role": "assistant",
            "content": "Reading it.",
            "tool_calls": turn.tool_calls,
        }

    def test_stream_turn_matches_non_stream(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_1", "model": "claude-test",
             "usage": {"input_tokens": 10, "cache_read_input_tokens": 3,
                       "cache_creation_input_tokens": 2, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "let me look"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "text_delta", "text": "Reading"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "text_delta", "text": " it."}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2,
             "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file"}},
            {"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.txt"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
             "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

        deltas: list[str] = []
        reasonings: list[str] = []
        with _mock(lambda request: httpx.Response(200, text=body)) as http:
            turn = anthropic.stream(
                ANTHROPIC_CONFIG,
                [{"role": "user", "content": "hi"}],
                on_delta=deltas.append,
                on_reasoning=reasonings.append,
                client=http,
            )

        assert deltas == ["Reading", " it."]
        assert reasonings == ["let me look"]
        assert turn.text == "Reading it."
        assert turn.reasoning == "let me look"
        assert turn.finish_reason == "tool_calls"
        assert turn.usage.prompt_tokens == 15
        assert turn.usage.completion_tokens == 5
        assert json.loads(turn.tool_calls[0]["function"]["arguments"]) == {"path": "a.txt"}

    def test_prompt_too_long_is_recognised(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": {"message": "prompt is too long: 300 tokens > 200"}}
            )

        with _mock(handler) as http:
            with pytest.raises(PromptTooLongError):
                anthropic.chat(
                    ANTHROPIC_CONFIG, [{"role": "user", "content": "hi"}], client=http
                )


# ---------------- Gemini generateContent ----------------


GEMINI_MESSAGES = [
    {"role": "user", "content": "hi"},
    {
        "role": "assistant",
        "content": "calling",
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_0", "content": "file data"},
    {"role": "user", "content": "thanks"},
]


class TestGemini:
    def test_request_shape(self):
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["req"] = request
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": "done"}]},
                         "finishReason": "STOP"}
                    ],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                },
            )

        with _mock(handler) as http:
            gemini.chat(
                GEMINI_CONFIG,
                GEMINI_MESSAGES,
                system="sys",
                tools=TOOLS,
                max_tokens=999,
                client=http,
            )

        request = captured["req"]
        assert (
            str(request.url)
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
        )
        assert request.headers["x-goog-api-key"] == "k"

        body = json.loads(request.content)
        assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
        assert body["generationConfig"] == {"maxOutputTokens": 999}
        assert body["tools"] == [
            {
                "functionDeclarations": [
                    {
                        "name": "read_file",
                        "description": "读一个文件",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    }
                ]
            }
        ]
        # functionResponse 按名字回给（Gemini 没有 tool_call id）；连续 user 合并。
        assert body["contents"] == [
            {"role": "user", "parts": [{"text": "hi"}]},
            {
                "role": "model",
                "parts": [
                    {"text": "calling"},
                    {"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}},
                ],
            },
            {
                "role": "user",
                "parts": [
                    {"functionResponse": {"name": "read_file", "response": {"result": "file data"}}},
                    {"text": "thanks"},
                ],
            },
        ]

    def test_turn_shape(self):
        data = {
            "modelVersion": "gemini-test",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "Reading it."},
                            {"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}},
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 20,
                "totalTokenCount": 120,
                "cachedContentTokenCount": 30,
                "thoughtsTokenCount": 7,
            },
        }

        with _mock(lambda request: httpx.Response(200, json=data)) as http:
            turn = gemini.chat(
                GEMINI_CONFIG, [{"role": "user", "content": "hi"}], client=http
            )

        assert turn.text == "Reading it."
        assert turn.finish_reason == "stop"
        assert turn.model == "gemini-test"
        assert turn.usage.prompt_tokens == 100  # Gemini 的 promptTokenCount 含缓存
        assert turn.usage.cache_read_tokens == 30
        assert turn.usage.reasoning_tokens == 7
        assert turn.tool_calls[0]["id"] == "call_0"
        assert json.loads(turn.tool_calls[0]["function"]["arguments"]) == {"path": "a.txt"}
        assert turn.message["role"] == "assistant"

    def test_stream_turn_matches_non_stream(self):
        chunks = [
            {"candidates": [{"content": {"parts": [{"text": "Reading"}]}}],
             "modelVersion": "gemini-test"},
            {"candidates": [{"content": {"parts": [{"text": " it."}]}}]},
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}}
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": 120,
                    "cachedContentTokenCount": 30,
                    "thoughtsTokenCount": 7,
                },
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)

        deltas: list[str] = []
        with _mock(lambda request: httpx.Response(200, text=body)) as http:
            turn = gemini.stream(
                GEMINI_CONFIG,
                [{"role": "user", "content": "hi"}],
                on_delta=deltas.append,
                client=http,
            )

        assert deltas == ["Reading", " it."]
        assert turn.text == "Reading it."
        assert turn.finish_reason == "stop"
        assert turn.model == "gemini-test"
        assert turn.usage.prompt_tokens == 100
        assert turn.tool_calls[0]["id"] == "call_0"
        assert json.loads(turn.tool_calls[0]["function"]["arguments"]) == {"path": "a.txt"}

    def test_max_tokens_exceeded_maps_to_length(self):
        data = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "half way"}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }

        with _mock(lambda request: httpx.Response(200, json=data)) as http:
            turn = gemini.chat(
                GEMINI_CONFIG, [{"role": "user", "content": "hi"}], client=http
            )
        assert turn.finish_reason == "length"

    def test_prompt_too_long_is_recognised(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "message": "input token count 1000 exceeds the maximum number of tokens allowed",
                    }
                },
            )

        with _mock(handler) as http:
            with pytest.raises(PromptTooLongError):
                gemini.chat(GEMINI_CONFIG, [{"role": "user", "content": "hi"}], client=http)


# ---------------- 门面分发 ----------------


class TestDispatch:
    def test_chat_completion_dispatches_by_provider(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "hi"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        config = Config(
            api_key="k",
            base_url="https://api.anthropic.com",
            model="claude-test",
            provider="anthropic",
        )
        with _mock(handler) as http:
            turn = chat_completion(config, [{"role": "user", "content": "hi"}], client=http)
        assert seen == ["https://api.anthropic.com/v1/messages"]
        assert turn.text == "hi"

    def test_stream_completion_dispatches_by_provider(self):
        events = [
            {"type": "message_start", "message": {"model": "claude-test",
             "usage": {"input_tokens": 1, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "hi"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
             "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

        config = Config(
            api_key="k",
            base_url="https://api.anthropic.com",
            model="claude-test",
            provider="anthropic",
        )
        with _mock(lambda request: httpx.Response(200, text=body)) as http:
            turn = stream_completion(config, [{"role": "user", "content": "hi"}], client=http)
        assert turn.text == "hi"

    def test_openai_path_unchanged_through_dispatcher(self):
        """provider 缺省 = openai 兼容：老路径（含 stream_options）逐字保留。"""
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["req"] = request
            return httpx.Response(
                200,
                json={
                    "model": "test-model",
                    "choices": [{"message": {"role": "assistant", "content": "hey"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        with _mock(handler) as http:
            turn = chat_completion(OPENAI_CONFIG, [{"role": "user", "content": "hi"}], client=http)
        assert turn.text == "hey"
        assert str(captured["req"].url) == "https://api.test/v1/chat/completions"

    def test_unknown_provider_raises(self):
        from avid.providers.config import ConfigError

        config = Config(api_key="k", base_url="https://x.test", model="m", provider="palm")
        with pytest.raises(ConfigError, match="palm"):
            chat_completion(config, [{"role": "user", "content": "hi"}])

    def test_ask_still_works_via_dispatcher(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "test-model",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        with _mock(handler) as http:
            reply = ask(OPENAI_CONFIG, "hi", client=http)
        assert reply.text == "ok"
