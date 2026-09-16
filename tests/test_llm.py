import httpx
import pytest

from avid.config import Config
from avid.llm import LLMError, PromptTooLongError, ask, build_payload, parse_reply

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
