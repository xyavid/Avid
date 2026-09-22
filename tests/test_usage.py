"""阶段 22：provider usage 的归一化与上下文窗口解析。

这一层只测 `ai/`：四家写法 → 同一个 ``Usage``（缺失一律 ``None``，不是 0）、
命中率的定义域，以及窗口的取值顺序（显式 > 内置表 > 不知道）。
派生量与落盘（``usage_report`` / 会话值 / HTTP）在 ``test_usage_ledger.py``。
"""

from __future__ import annotations

import pytest

from avid.ai.config import (
    ENV_CONTEXT_WINDOW,
    ConfigError,
    load_config,
    window_for,
)
from avid.ai.usage import Usage, hit_ratio, normalize_usage

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
    "gemini": (
        {
            "usageMetadata": {
                "promptTokenCount": 90,
                "candidatesTokenCount": 4,
                "totalTokenCount": 94,
                "cachedContentTokenCount": 30,
            }
        },
        Usage(90, 4, 94, cache_read_tokens=30),
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
    assert load_config({"AVID_API_KEY": "k", "AVID_MODEL": "test-model"}).context_window is None


def test_explicit_window_wins_and_falls_back_to_the_table():
    explicit = load_config(
        {"AVID_API_KEY": "k", "AVID_MODEL": "test-model", ENV_CONTEXT_WINDOW: "12345"}
    )
    assert explicit.context_window == 12345
    fallback = load_config({"AVID_API_KEY": "k", "AVID_MODEL": "gpt-4o-mini"})
    assert fallback.context_window == 128_000


@pytest.mark.parametrize("raw", ["128k", "0", "-1", ""])
def test_invalid_explicit_window_fails_loudly(raw):
    """非法值必须报错而不是静默回落：静默回落会让用户以为看的是自己设的分母。"""
    env = {"AVID_API_KEY": "k", "AVID_MODEL": "gpt-4o-mini", ENV_CONTEXT_WINDOW: raw}
    if raw == "":
        # 空串 = 没设：走内置表。
        assert load_config(env).context_window == 128_000
        return
    with pytest.raises(ConfigError):
        load_config(env)
