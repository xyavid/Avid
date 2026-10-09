"""The runtime face of ``providers/config.py``: the Config carrier, the window table, two switches.

Model connection settings (endpoint / protocol / key / model) come only from BYOK; this file keeps
to the model-independent parts: ``Config`` derived attributes and invariants, the built-in window
table, the ``AVID_MODEL_INFO`` probe switch and the ``AVID_MAX_PARALLEL_TOOL_CALLS`` cap.
"""

import pytest

from avid.providers.config import (
    Config,
    ConfigError,
    max_parallel_tool_calls,
    model_info_enabled,
)


def make_config(**overrides) -> Config:
    fields = {"api_key": "k", "base_url": "https://api.test/v1", "model": "m"}
    fields.update(overrides)
    return Config(**fields)


# ---------- Config carrier ----------


def test_chat_completions_url_normalises_trailing_slash():
    config = make_config(base_url="https://api.test/v1/")

    assert config.chat_completions_url == "https://api.test/v1/chat/completions"


def test_config_carries_no_round_limit():
    """There is no round limit: the kernel converges when the model stops requesting tools."""
    assert not hasattr(make_config(), "max_rounds")


def test_resolved_provider_rejects_unknown_family():
    with pytest.raises(ConfigError, match="provider"):
        _ = make_config(provider="bogus").resolved_provider


# ---------- AVID_MAX_PARALLEL_TOOL_CALLS ----------


def test_parallel_tool_calls_default_to_ten():
    assert make_config().max_parallel_tool_calls == 10
    assert max_parallel_tool_calls({}) == 10


def test_parallel_tool_calls_can_be_set_to_one():
    """1 is a legal value: fully serial."""
    assert max_parallel_tool_calls({"AVID_MAX_PARALLEL_TOOL_CALLS": "1"}) == 1


@pytest.mark.parametrize("raw", ["0", "-3", "很多", "3.5"])
def test_parallel_tool_calls_rejects_non_positive_or_non_integer(raw):
    with pytest.raises(ConfigError) as exc:
        max_parallel_tool_calls({"AVID_MAX_PARALLEL_TOOL_CALLS": raw})

    assert "AVID_MAX_PARALLEL_TOOL_CALLS" in str(exc.value)


def test_parallel_tool_calls_refuses_to_silently_clamp():
    """Above the hard cap (32) it errors instead of clamping, which would be harder to diagnose."""
    with pytest.raises(ConfigError) as exc:
        max_parallel_tool_calls({"AVID_MAX_PARALLEL_TOOL_CALLS": "1000"})

    assert "32" in str(exc.value)


# ---------- model info probe switch ----------


def test_model_info_probe_defaults_on_and_can_be_turned_off():
    assert model_info_enabled({}) is True
    for raw in ("off", "0", "false", "no", "OFF"):
        assert model_info_enabled({"AVID_MODEL_INFO": raw}) is False


# ---------- built-in window table ----------


def test_window_table_covers_prefixes_and_family_fallbacks():
    """The window table matches the longest prefix: a concrete entry beats the family fallback."""
    from avid.providers.config import window_for

    assert window_for("gpt-4.1") == 1_047_576
    assert window_for("claude-sonnet-4-5") == 200_000  # the family fallback covers unlisted models
    assert window_for("mystery-model") is None
