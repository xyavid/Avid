import pytest

from avid.ai.config import DEFAULT_BASE_URL, ConfigError, load_config


def test_missing_variables_are_named_with_a_fix():
    with pytest.raises(ConfigError) as exc:
        load_config({"AVID_MODEL": "deepseek-chat"})

    message = str(exc.value)
    assert "AVID_API_KEY" in message
    assert "AVID_MODEL" not in message  # 只报缺失的那一个
    assert "cp .env.example .env" in message


def test_base_url_falls_back_to_default():
    config = load_config({"AVID_API_KEY": "k", "AVID_MODEL": "m"})

    assert config.base_url == DEFAULT_BASE_URL
    assert config.chat_completions_url == f"{DEFAULT_BASE_URL}/chat/completions"


def test_trailing_slash_in_base_url_is_normalised():
    config = load_config(
        {"AVID_API_KEY": "k", "AVID_MODEL": "m", "AVID_BASE_URL": "https://api.test/v1/"}
    )

    assert config.chat_completions_url == "https://api.test/v1/chat/completions"


def test_config_carries_no_round_limit():
    """设置里没有轮数上限这一项：配置对象不该再带 `max_rounds`。

    图里的「Agent 循环」只控制一步内的并发调用数，没有轮次；轮数不是收敛判据，
    内核靠"模型不再请求工具"收敛。
    """
    config = load_config({"AVID_API_KEY": "k", "AVID_MODEL": "m"})

    assert not hasattr(config, "max_rounds")


# ---------- AVID_MAX_PARALLEL_TOOL_CALLS ----------


def test_parallel_tool_calls_default_to_ten():
    config = load_config({"AVID_API_KEY": "k", "AVID_MODEL": "m"})

    assert config.max_parallel_tool_calls == 10


def test_parallel_tool_calls_can_be_set_to_one():
    """1 是合法值：完全串行，改动前的行为。"""
    config = load_config(
        {"AVID_API_KEY": "k", "AVID_MODEL": "m", "AVID_MAX_PARALLEL_TOOL_CALLS": "1"}
    )

    assert config.max_parallel_tool_calls == 1


@pytest.mark.parametrize("raw", ["0", "-3", "很多", "3.5"])
def test_parallel_tool_calls_rejects_non_positive_or_non_integer(raw):
    with pytest.raises(ConfigError) as exc:
        load_config(
            {
                "AVID_API_KEY": "k",
                "AVID_MODEL": "m",
                "AVID_MAX_PARALLEL_TOOL_CALLS": raw,
            }
        )

    assert "AVID_MAX_PARALLEL_TOOL_CALLS" in str(exc.value)


def test_parallel_tool_calls_refuses_to_silently_clamp():
    """超过硬上限报错而不是夹取：设了 1000 却按 32 跑，比报错更难查。"""
    with pytest.raises(ConfigError) as exc:
        load_config(
            {
                "AVID_API_KEY": "k",
                "AVID_MODEL": "m",
                "AVID_MAX_PARALLEL_TOOL_CALLS": "1000",
            }
        )

    assert "32" in str(exc.value)
