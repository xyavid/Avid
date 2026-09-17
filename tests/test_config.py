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
