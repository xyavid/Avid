import httpx
import pytest

from avid.config import Config
from avid.llm import LLMError, ask, build_payload, parse_reply

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
