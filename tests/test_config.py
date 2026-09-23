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


# ---------- AVID_MAX_ROUNDS ----------


def test_max_rounds_defaults_to_no_limit():
    """缺省无上限：轮数不是预算，循环靠"模型不再请求工具"收敛。"""
    config = load_config({"AVID_API_KEY": "k", "AVID_MODEL": "m"})

    assert config.max_rounds is None


@pytest.mark.parametrize("raw", ["0", "off", "OFF", "false", "no", "none", "unlimited"])
def test_max_rounds_off_values_mean_no_limit(raw):
    config = load_config({"AVID_API_KEY": "k", "AVID_MODEL": "m", "AVID_MAX_ROUNDS": raw})

    assert config.max_rounds is None


def test_max_rounds_positive_value_is_kept():
    config = load_config(
        {"AVID_API_KEY": "k", "AVID_MODEL": "m", "AVID_MAX_ROUNDS": "40"}
    )

    assert config.max_rounds == 40


@pytest.mark.parametrize("raw", ["很多", "3.5", "-1", "1e3"])
def test_max_rounds_invalid_value_fails_loudly(raw):
    """非法值报错而不是回落成无上限：静默忽略会让用户以为自己设了闸门。"""
    with pytest.raises(ConfigError) as exc:
        load_config({"AVID_API_KEY": "k", "AVID_MODEL": "m", "AVID_MAX_ROUNDS": raw})

    assert "AVID_MAX_ROUNDS" in str(exc.value)
