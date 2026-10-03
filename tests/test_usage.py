"""阶段 22：provider usage 的归一化与上下文窗口解析。

这一层只测 `ai/`：四家写法 → 同一个 ``Usage``（缺失一律 ``None``，不是 0）、
命中率的定义域，以及内置窗口表（显式声明的窗口来自 BYOK 模型声明，
解析链在 ``test_ai_byok.py``）。
派生量与落盘（``usage_report`` / 会话值 / HTTP）在 ``test_usage_ledger.py``。
"""

from __future__ import annotations

import pytest

from avid.providers.config import Config, window_for
from avid.providers.usage import Usage, hit_ratio, normalize_usage

# ---------------- provider adapter ----------------

#: 四家的真实形状（字段名照各家文档，值取整便于断言）。
#: 值得注意的两处口径差异：Anthropic 的 `input_tokens` 不含缓存部分，
#: 所以它换算出来的 prompt_tokens 是 `input + read + write`。
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
    """OpenAI 自动缓存不上报写入计数：缺失就是缺失，界面显示「—」而不是 0。"""
    usage = normalize_usage({"usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}})
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert hit_ratio(usage) is None


def test_bare_usage_object_is_accepted():
    """上游只回一小段 JSON（没有信封）时也认——否则调用方要自己拼一层。"""
    assert normalize_usage({"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}) == Usage(3, 1, 4)


def test_unrecognized_payload_is_all_zero_and_does_not_raise():
    """usage 只是观测：认不出来不该让一次成功的调用失败。"""
    assert normalize_usage({}) == Usage(0, 0, 0)
    assert normalize_usage(None) == Usage(0, 0, 0)
    assert normalize_usage({"usage": "nonsense"}) == Usage(0, 0, 0)
    # 只认得总量时把总量留下，其余归零。
    assert normalize_usage({"usage": {"total_tokens": 42}}) == Usage(0, 0, 42)


def test_string_and_float_counters_are_coerced_bool_is_not():
    """网关把计数写成字符串是常见的；`True` 是 int 的子类，必须排掉。"""
    usage = normalize_usage({"usage": {"prompt_tokens": "12", "completion_tokens": 3.0, "total_tokens": 15}})
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 3
    assert normalize_usage({"usage": {"prompt_tokens": True, "total_tokens": 1}}).prompt_tokens == 0


def test_missing_total_is_derived_from_prompt_and_completion():
    usage = normalize_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    assert usage.total_tokens == 12


# ---------------- 命中率 ----------------

def test_hit_ratio_denominator_is_the_whole_input():
    assert hit_ratio(Usage(100, 0, 100, cache_read_tokens=50)) == 0.5
    assert hit_ratio(Usage(1000, 0, 1000, cache_read_tokens=778)) == 0.778


def test_hit_ratio_is_none_without_cache_data_or_input():
    assert hit_ratio(Usage(100, 0, 100)) is None
    assert hit_ratio(Usage(0, 0, 0, cache_read_tokens=0)) is None


def test_hit_ratio_is_clamped_when_upstream_reports_more_cache_than_input():
    """上游谎报时截断成 1.0：界面上"超过 100% 的命中率"只会被当成 bug。"""
    assert hit_ratio(Usage(10, 0, 10, cache_read_tokens=99)) == 1.0


# ---------------- 窗口解析 ----------------

def test_window_table_uses_longest_prefix():
    assert window_for("gpt-4o-mini") == 128_000
    assert window_for("gpt-4.1-mini") == 1_047_576
    assert window_for("claude-sonnet-4-20250514") == 200_000
    assert window_for("GPT-4O") == 128_000  # 大小写无关


def test_unknown_model_has_no_window_instead_of_a_guess():
    assert window_for("test-model") is None


# ---------------- 窗口探测（问 provider 的 /models） ----------------

@pytest.fixture
def probe_on(monkeypatch):
    """打开探测：conftest 对**每个**用例都设了 `AVID_MODEL_INFO=off`（不打真实端点），
    要验探测本身的用例在这里把它删掉——缺省即开。"""
    monkeypatch.delenv("AVID_MODEL_INFO", raising=False)


@pytest.fixture
def probe_cache():
    """清掉进程内探测缓存：它是跨用例的（真实运行时正是靠它只问一次）。"""
    from avid.providers import client as client_module

    with client_module._MODEL_WINDOW_LOCK:
        client_module._MODEL_WINDOWS.clear()
    yield
    with client_module._MODEL_WINDOW_LOCK:
        client_module._MODEL_WINDOWS.clear()


def model_listing(*entries):
    return {"data": [dict(entry) for entry in entries]}


def probe(config, payload, *, status=200, transport_calls=None):
    """用 MockTransport 跑一次探测，返回 (窗口, transport 调用次数)。"""
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
    """自建网关/新模型的窗口只有服务商知道：问一次 /models 就能算占用率了。"""
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
        (model_listing({"id": "vendor/other", "context_length": 8_000}), 200),  # 没列这个模型
        (model_listing({"id": "vendor/x-flash"}), 200),  # 列了但没有窗口字段
        ({"error": "nope"}, 401),  # 鉴权失败
        ("<html>gateway</html>", 200),  # 不是 JSON
        ({"data": "nonsense"}, 200),  # 结构不合约定
    ],
)
def test_probe_returns_none_on_anything_unusable(probe_cache, probe_on, payload, status):
    """问不到就是没有：不抛错、不影响模型调用，界面照旧只报 tokens。"""
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
    """同进程只问一次（成功与失败都缓存）：一次运行里被反复调用也不打端点。"""
    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    payload = model_listing({"id": "vendor/x-flash", "context_length": 200_000})
    first, calls = probe(config, payload)
    second, _ = probe(config, payload, transport_calls=calls)
    assert first == second == 200_000
    assert calls == ["https://gw.test/v1/models"]  # 第二次没再发请求


def test_probe_can_be_switched_off(probe_cache, monkeypatch):
    """`AVID_MODEL_INFO=off` 时不联网：单测与明确不想探测的部署都靠它。"""
    monkeypatch.setenv("AVID_MODEL_INFO", "off")
    config = Config(api_key="test-key", base_url="https://gw.test/v1", model="vendor/x-flash")
    window, calls = probe(config, model_listing({"id": "vendor/x-flash", "context_length": 1}))
    assert window is None
    assert calls == []


def test_reasoning_tokens_are_read_from_the_nested_details():
    """A2：推理 token 是 completion 的子集，OpenAI 兼容写法放在 details 里。"""
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
    """有的网关把 reasoning_tokens 直接放在 usage 顶层。"""
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
    """没有这个数就是 None：0 会被界面读成"思考了零个 token"。"""
    usage = normalize_usage(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
    )

    assert usage.reasoning_tokens is None
