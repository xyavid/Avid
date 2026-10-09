"""Usage normalization across the four provider dialects and the built-in window table.

Missing counters normalize to ``None``, never 0, so an unrecognized payload cannot read as an
empty context; declared windows come from BYOK models, and derived/HTTP views live in
``test_usage_ledger.py``.
"""

from __future__ import annotations

import pytest

from avid.providers.config import Config, window_for
from avid.providers.usage import Usage, hit_ratio, normalize_usage

# ---------------- provider adapters ----------------

#: Real shapes from each provider's docs (values rounded to keep assertions readable); note
#: Anthropic's `input_tokens` excludes cache, so its prompt_tokens is input + read + write.
DIALECTS = {
    "openai": (
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                "prompt_tokens_details": {"cached_tokens": 64},
            }
        },
        Usage(100, 5, 105, cache_read_tokens=64),
    ),
    "deepseek": (
        {
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 7,
                "total_tokens": 207,
                "prompt_cache_hit_tokens": 180,
                "prompt_cache_miss_tokens": 20,
            }
        },
        Usage(200, 7, 207, cache_read_tokens=180),
    ),
    "anthropic": (
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 3,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 8,
            }
        },
        Usage(58, 3, 61, cache_read_tokens=40, cache_write_tokens=8),
    ),
    "responses": (
        {
            "usage": {
                "input_tokens": 90,
                "output_tokens": 4,
                "total_tokens": 94,
                "input_tokens_details": {"cached_tokens": 30},
                "output_tokens_details": {"reasoning_tokens": 2},
            }
        },
        Usage(90, 4, 94, cache_read_tokens=30, reasoning_tokens=2),
    ),
}


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_four_dialects_normalize_to_one_shape(dialect):
    raw, expected = DIALECTS[dialect]
    assert normalize_usage(raw) == expected


def test_missing_cache_counters_are_none_not_zero():
    """OpenAI's automatic cache omits the write counter: missing stays missing, never 0."""
    usage = normalize_usage({"usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}})
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert hit_ratio(usage) is None


def test_bare_usage_object_is_accepted():
    """A bare counters object (no ``usage`` envelope) is accepted too."""
    assert normalize_usage({"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}) == Usage(3, 1, 4)


def test_unrecognized_payload_is_all_zero_and_does_not_raise():
    """Usage is observation only: an unrecognized payload must not fail a successful call."""
    assert normalize_usage({}) == Usage(0, 0, 0)
    assert normalize_usage(None) == Usage(0, 0, 0)
    assert normalize_usage({"usage": "nonsense"}) == Usage(0, 0, 0)
    # Total-only payload keeps the total and zeroes the rest.
    assert normalize_usage({"usage": {"total_tokens": 42}}) == Usage(0, 0, 42)


def test_string_and_float_counters_are_coerced_bool_is_not():
    """Gateways often send counters as strings; ``True`` is an int subclass and is excluded."""
    usage = normalize_usage({"usage": {"prompt_tokens": "12", "completion_tokens": 3.0, "total_tokens": 15}})
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 3
    assert normalize_usage({"usage": {"prompt_tokens": True, "total_tokens": 1}}).prompt_tokens == 0


def test_missing_total_is_derived_from_prompt_and_completion():
    usage = normalize_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    assert usage.total_tokens == 12


# ---------------- hit ratio ----------------

def test_hit_ratio_denominator_is_the_whole_input():
    assert hit_ratio(Usage(100, 0, 100, cache_read_tokens=50)) == 0.5
    assert hit_ratio(Usage(1000, 0, 1000, cache_read_tokens=778)) == 0.778


def test_hit_ratio_is_none_without_cache_data_or_input():
    assert hit_ratio(Usage(100, 0, 100)) is None
    assert hit_ratio(Usage(0, 0, 0, cache_read_tokens=0)) is None


def test_hit_ratio_is_clamped_when_upstream_reports_more_cache_than_input():
    """Upstream over-reporting is clamped to 1.0."""
    assert hit_ratio(Usage(10, 0, 10, cache_read_tokens=99)) == 1.0


# ---------------- window resolution ----------------

def test_window_table_uses_longest_prefix():
    assert window_for("gpt-4o-mini") == 128_000
    assert window_for("gpt-4.1-mini") == 1_047_576
    assert window_for("claude-sonnet-4-20250514") == 200_000
    assert window_for("GPT-4O") == 128_000  # case-insensitive


def test_unknown_model_has_no_window_instead_of_a_guess():
    assert window_for("test-model") is None


# ---------------- window probing (the provider's /models) ----------------

@pytest.fixture
def probe_on(monkeypatch):
    """Remove conftest's per-test ``AVID_MODEL_INFO=off`` (probing defaults to on)."""
    monkeypatch.delenv("AVID_MODEL_INFO", raising=False)


@pytest.fixture
def probe_cache():
    """Clear the process-wide probe cache, which otherwise leaks across tests."""
    from avid.providers import client as client_module

    with client_module._MODEL_WINDOW_LOCK:
        client_module._MODEL_WINDOWS.clear()
    yield
    with client_module._MODEL_WINDOW_LOCK:
        client_module._MODEL_WINDOWS.clear()


def model_listing(*entries):
    return {"data": [dict(entry) for entry in entries]}


def probe(config, payload, *, status=200, transport_calls=None):
    """Run one probe over MockTransport; returns (window, transport calls)."""
    import httpx

    from avid.providers.client import fetch_context_length

    calls = transport_calls if transport_calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer test-key"
        if not isinstance(payload, dict):
            return httpx.Response(status, text=str(payload))
        return httpx.Response(status, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return fetch_context_length(config, client=http), calls


def test_probe_reads_context_length_from_the_provider(probe_cache, probe_on):
    """A window unknown to the table comes from one ``/models`` query to the provider."""
    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    payload = model_listing(
        {"id": "vendor/other", "context_length": 8_000},
        {"id": "vendor/x-flash", "context_length": 1_000_000},
    )
    window, calls = probe(config, payload)
    assert window == 1_000_000
    assert calls == ["https://gw.test/v1/models"]


@pytest.mark.parametrize(
    "payload,status",
    [
        (model_listing({"id": "vendor/other", "context_length": 8_000}), 200),  # not listed
        (model_listing({"id": "vendor/x-flash"}), 200),  # listed, no window field
        ({"error": "nope"}, 401),  # auth failure
        ("<html>gateway</html>", 200),  # not JSON
        ({"data": "nonsense"}, 200),  # wrong shape
    ],
)
def test_probe_returns_none_on_anything_unusable(probe_cache, probe_on, payload, status):
    """Not found means none: no raise and no impact on the model call."""
    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    window, _ = probe(config, payload, status=status)
    assert window is None


def test_probe_never_raises_on_network_error(probe_cache, probe_on):
    import httpx

    from avid.providers.client import fetch_context_length

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    with httpx.Client(transport=httpx.MockTransport(boom)) as http:
        assert fetch_context_length(config, client=http) is None


def test_probe_is_cached_and_idempotent(probe_cache, probe_on):
    """Asked once per process, with success and failure both cached."""
    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    payload = model_listing({"id": "vendor/x-flash", "context_length": 200_000})
    first, calls = probe(config, payload)
    second, _ = probe(config, payload, transport_calls=calls)
    assert first == second == 200_000
    assert calls == ["https://gw.test/v1/models"]  # no second request


def test_probe_can_be_switched_off(probe_cache, monkeypatch):
    """``AVID_MODEL_INFO=off`` disables probing entirely (no network)."""
    monkeypatch.setenv("AVID_MODEL_INFO", "off")
    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    window, calls = probe(config, model_listing({"id": "vendor/x-flash", "context_length": 1}))
    assert window is None
    assert calls == []


def test_reasoning_tokens_are_read_from_the_nested_details():
    """Reasoning tokens are a subset of completion, nested in ``*_tokens_details``."""
    usage = normalize_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 90,
                "total_tokens": 100,
                "completion_tokens_details": {"reasoning_tokens": 64},
            }
        }
    )

    assert usage.reasoning_tokens == 64


def test_a_flat_reasoning_token_field_is_accepted_too():
    """Some gateways put ``reasoning_tokens`` flat at the top of usage."""
    usage = normalize_usage(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 9, "reasoning_tokens": 7}}
    )

    assert usage.reasoning_tokens == 7


def test_responses_reasoning_details_count_as_reasoning_tokens():
    usage = normalize_usage(
        {
            "usage": {
                "input_tokens": 3,
                "output_tokens": 45,
                "total_tokens": 48,
                "output_tokens_details": {"reasoning_tokens": 40},
            }
        }
    )

    assert usage.reasoning_tokens == 40


def test_missing_reasoning_tokens_stay_none():
    """Absent reasoning count stays ``None``; 0 would read as zero tokens thought."""
    usage = normalize_usage(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
    )

    assert usage.reasoning_tokens is None
