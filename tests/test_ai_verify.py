"""BYOK connectivity check: a chat probe at ``max_tokens=1``, then a tool smoke test.

The smoke step fails unless the model returns tool_calls; errors are classified only from known
LLMError signals (HTTP status, network wording) and ConfigError is reported rather than raised.
"""

from __future__ import annotations

import pytest

from avid.providers import verify as verify_module
from avid.providers.byok import ModelCapabilities, ModelDecl, ProviderDecl, set_secret
from avid.providers.config import ConfigError
from avid.providers.protocol import LLMError, Turn, Usage


@pytest.fixture(autouse=True)
def wired_secret(tmp_path, monkeypatch):
    """Point the secrets file at a temp dir with one entry, so verify probes a wired endpoint."""
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(tmp_path / "secrets.json"))
    set_secret("deepseek", "sk-test")


def provider_decl(**overrides) -> ProviderDecl:
    fields = {
        "id": "deepseek",
        "label": "DeepSeek",
        "protocol": "openai-compatible",
        "base_url": "https://api.deepseek.example/v1",
        "models": (ModelDecl(id="deepseek-chat", capabilities=ModelCapabilities(tool_calling=True)),),
    }
    fields.update(overrides)
    return ProviderDecl(**fields)


def make_turn(tool_calls: list[dict] | None = None) -> Turn:
    return Turn(
        message={"role": "assistant", "content": "ok"},
        text="ok",
        tool_calls=tool_calls or [],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model="deepseek-chat",
        finish_reason="stop",
    )


def script_chat(steps: list[Turn | Exception]):
    """A fake chat_completion that yields Turns (or raises) in call order."""

    def fake(config, messages, *, system=None, tools=None, max_tokens=None, client=None):
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return fake


# ---------------- both steps pass ----------------


def test_both_steps_pass(monkeypatch):
    seen = []

    def fake(config, messages, *, system=None, tools=None, max_tokens=None, client=None):
        seen.append({"tools": tools, "max_tokens": max_tokens})
        return make_turn([{"id": "t1", "function": {"name": "get_time"}}] if tools else [])

    monkeypatch.setattr(verify_module, "chat_completion", fake)

    report = verify_module.verify_provider(provider_decl(), "deepseek-chat")

    assert report.ok is True
    assert [s.step for s in report.steps] == ["chat", "tool"]
    assert all(s.ok for s in report.steps)
    # chat probe: max_tokens=1 and no tools; smoke: a tool definition is required
    assert seen[0]["max_tokens"] == 1 and seen[0]["tools"] is None
    assert seen[1]["tools"] and seen[1]["max_tokens"] != 1


# ---------------- smoke failure ----------------


def test_chat_works_but_no_tool_calls_fails_the_smoke_step(monkeypatch):
    monkeypatch.setattr(
        verify_module, "chat_completion", script_chat([make_turn([]), make_turn([])])
    )

    report = verify_module.verify_provider(provider_decl(), "deepseek-chat")

    assert report.ok is False
    chat, tool = report.steps
    assert chat.ok is True
    assert tool.ok is False
    assert "工具" in tool.detail


# ---------------- error classification ----------------


@pytest.mark.parametrize(
    ("message", "signal"),
    [
        ("HTTP 401 — invalid key", "密钥"),
        ("HTTP 403 — forbidden", "密钥"),
        ("HTTP 404 — Not Found", "/v1"),
        ("HTTP 429 — rate limited", "限流"),
        ("连接 https://x 失败：timed out", "连不上"),
        ("HTTP 500 — boom", "HTTP 500"),
    ],
)
def test_llm_errors_are_classified(monkeypatch, message, signal):
    monkeypatch.setattr(
        verify_module, "chat_completion", script_chat([LLMError(message)])
    )

    report = verify_module.verify_provider(provider_decl(), "deepseek-chat")

    assert report.ok is False
    chat = report.steps[0]
    assert chat.ok is False
    assert signal in chat.detail


def test_config_error_reports_instead_of_raising(monkeypatch):
    def fake(config, messages, **_):
        raise AssertionError("配置解析失败时不应发起请求")

    monkeypatch.setattr(verify_module, "chat_completion", fake)

    def boom():
        raise ConfigError("chat 槽位还没有绑定模型")

    from avid.providers import byok as byok_module

    monkeypatch.setattr(byok_module, "config_from_provider", lambda *a, **k: boom())

    report = verify_module.verify_provider(provider_decl(), "deepseek-chat")

    assert report.ok is False
    assert "绑定" in report.steps[0].detail
