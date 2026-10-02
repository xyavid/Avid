"""`ai/config.py` 的运行期面：Config 载体、窗口表与两个旁路开关。

模型连接（端点 / 协议 / 密钥 / 模型名）只来自 BYOK（`tests/test_ai_byok.py` 守那
一侧）；这里只测模型无关的部分——`Config` 的派生属性与不变量、内置窗口表、
`AVID_MODEL_INFO` 探测开关与 `AVID_MAX_PARALLEL_TOOL_CALLS` 并行上限。
"""

import pytest

from avid.ai.config import (
    KNOWN_MODELS,
    Config,
    ConfigError,
    max_parallel_tool_calls,
    model_info_enabled,
)


def make_config(**overrides) -> Config:
    fields = {"api_key": "k", "base_url": "https://api.test/v1", "model": "m"}
    fields.update(overrides)
    return Config(**fields)


# ---------- Config 载体 ----------


def test_chat_completions_url_normalises_trailing_slash():
    config = make_config(base_url="https://api.test/v1/")

    assert config.chat_completions_url == "https://api.test/v1/chat/completions"


def test_config_carries_no_round_limit():
    """设置里没有轮数上限这一项：配置对象不该再带 `max_rounds`。

    图里的「Agent 循环」只控制一步内的并发调用数，没有轮次；轮数不是收敛判据，
    内核靠"模型不再请求工具"收敛。
    """
    assert not hasattr(make_config(), "max_rounds")


def test_resolved_provider_rejects_unknown_family():
    with pytest.raises(ConfigError, match="provider"):
        _ = make_config(provider="bogus").resolved_provider


# ---------- AVID_MAX_PARALLEL_TOOL_CALLS ----------


def test_parallel_tool_calls_default_to_ten():
    assert make_config().max_parallel_tool_calls == 10
    assert max_parallel_tool_calls({}) == 10


def test_parallel_tool_calls_can_be_set_to_one():
    """1 是合法值：完全串行，改动前的行为。"""
    assert max_parallel_tool_calls({"AVID_MAX_PARALLEL_TOOL_CALLS": "1"}) == 1


@pytest.mark.parametrize("raw", ["0", "-3", "很多", "3.5"])
def test_parallel_tool_calls_rejects_non_positive_or_non_integer(raw):
    with pytest.raises(ConfigError) as exc:
        max_parallel_tool_calls({"AVID_MAX_PARALLEL_TOOL_CALLS": raw})

    assert "AVID_MAX_PARALLEL_TOOL_CALLS" in str(exc.value)


def test_parallel_tool_calls_refuses_to_silently_clamp():
    """超过硬上限报错而不是夹取：设了 1000 却按 32 跑，比报错更难查。"""
    with pytest.raises(ConfigError) as exc:
        max_parallel_tool_calls({"AVID_MAX_PARALLEL_TOOL_CALLS": "1000"})

    assert "32" in str(exc.value)


# ---------- 模型信息探测开关 ----------


def test_model_info_probe_defaults_on_and_can_be_turned_off():
    assert model_info_enabled({}) is True
    for raw in ("off", "0", "false", "no", "OFF"):
        assert model_info_enabled({"AVID_MODEL_INFO": raw}) is False


# ---------- 内置窗口表 ----------


def test_known_models_excludes_family_fallbacks():
    """界面候选表不能把 `claude-` 这种族回退当模型 id 列出来。"""
    assert "deepseek-chat" in KNOWN_MODELS and "deepseek-reasoner" in KNOWN_MODELS
    assert all(not name.endswith("-") for name in KNOWN_MODELS)
