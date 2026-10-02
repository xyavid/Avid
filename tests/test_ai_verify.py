"""BYOK 连通校验：两步（最小对话 + 工具冒烟）与错误分类。

契约要点：
- ① 最小对话 `max_tokens=1`，验证密钥（若已配置）、端点与网络；
- ② 工具冒烟带一个工具定义，模型没回 tool_calls 就是失败——「能聊天不能干活」
  的模型在这一步被筛掉，而不是接进 agent 后每次运行都废；
- 错误分类只认 LLMError 消息里的已知信号（HTTP 状态码 / 网络措辞），认不出时原样
  透出消息摘录，绝不编造原因；
- 配置解析失败（ConfigError）不算端点问题，也走报告而不是异常——调用方是设置界面。
"""

from __future__ import annotations

import pytest

from avid.ai import verify as verify_module
from avid.ai.byok import ModelCapabilities, ModelDecl, ProviderDecl, set_secret
from avid.ai.config import ConfigError
from avid.ai.protocol import LLMError, Turn, Usage


@pytest.fixture(autouse=True)
def wired_secret(tmp_path, monkeypatch):
    """密钥文件指到临时目录并预置一条：verify 走的是「密钥齐全后的端点探测」。"""
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
    """按调用次序吐 Turn 或抛异常的假 chat_completion。"""

    def fake(config, messages, *, system=None, tools=None, max_tokens=None, client=None):
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return fake


# ---------------- 两步通过 ----------------


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
    # ① 最小对话必须 max_tokens=1 且不带工具；② 冒烟必须带工具定义
    assert seen[0]["max_tokens"] == 1 and seen[0]["tools"] is None
    assert seen[1]["tools"] and seen[1]["max_tokens"] != 1


# ---------------- 冒烟失败 ----------------


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


# ---------------- 错误分类 ----------------


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

    from avid.ai import byok as byok_module

    monkeypatch.setattr(byok_module, "config_from_provider", lambda *a, **k: boom())

    report = verify_module.verify_provider(provider_decl(), "deepseek-chat")

    assert report.ok is False
    assert "绑定" in report.steps[0].detail
