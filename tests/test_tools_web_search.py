"""web_search 的分支覆盖：成功、空结果、四类失败、参数收口与截断。

全部走 ``httpx.MockTransport`` 注入——**不发真实网络请求**，因此用例不依赖网络、
不消耗 Tavily 额度，CI 里也是确定的。真实端点只由一条手工冒烟命令覆盖（见
``docs/status/CAPABILITIES.md`` 与阶段 21 的验收证据）。

约定类断言（前缀 ``错误：``、失败不抛异常）在这里按"模型看到什么"来断言，
而不是按内部实现：这些文本就是回传给模型的东西。
"""

from __future__ import annotations

import json

import httpx
import pytest

from avid.tools.search_config import (
    ENV_API_KEY,
    ENV_BASE_URL,
    SearchConfigError,
    load_search_config,
)
from avid.tools.web_search import (
    MAX_SNIPPET_CHARS,
    web_search,
)


def _payload(**overrides):
    body = {
        "query": "Avid agent runtime",
        "results": [
            {
                "title": "Avid 项目",
                "url": "https://example.com/avid",
                "content": "Avid 是一个自建 agent 运行时。",
                "score": 0.91,
            },
            {
                "title": "另一个来源",
                "url": "https://example.org/two",
                "content": "关于 harness 的说明。",
                "score": 0.42,
            },
        ],
    }
    body.update(overrides)
    return body


def _client(handler) -> httpx.Client:
    """把一段处理函数包成可注入的 client（捕获请求由 handler 自己负责）。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(**overrides):
    return _client(lambda request: httpx.Response(200, json=_payload(**overrides)))


@pytest.fixture(autouse=True)
def tavily_key(monkeypatch):
    """每个用例都有一把"看起来可用"的检索 Key；需要测缺 Key 的用例自己删掉它。

    与 ``conftest.model_env`` 同一个理由：缺配置是**一条**显式用例，不该是其余
    用例的隐含前提。
    """
    monkeypatch.setenv(ENV_API_KEY, "tvly-test-key")
    monkeypatch.delenv(ENV_BASE_URL, raising=False)


# ---------- 配置 ----------


def test_missing_key_is_reported_with_a_fix(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)

    result = web_search({"query": "anything"}, client=_ok())

    assert result.startswith("错误：")
    assert ENV_API_KEY in result  # 说清该设哪个变量
    assert ".env" in result  # 并给出设置方法


def test_load_search_config_requires_a_key():
    with pytest.raises(SearchConfigError) as excinfo:
        load_search_config({"TAVILY_BASE_URL": "https://api.tavily.com"})

    assert ENV_API_KEY in str(excinfo.value)


def test_load_search_config_defaults_the_base_url():
    config = load_search_config({ENV_API_KEY: "tvly-x"})

    assert config.api_key == "tvly-x"
    assert config.search_url == "https://api.tavily.com/search"


def test_load_search_config_honours_the_base_url_override():
    config = load_search_config(
        {ENV_API_KEY: "tvly-x", ENV_BASE_URL: "http://127.0.0.1:9999/"}
    )

    # 尾斜杠不该被复制成两条：`//search` 会被服务端当成另一个路径。
    assert config.search_url == "http://127.0.0.1:9999/search"


def test_a_blank_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "   ")

    assert web_search({"query": "x"}, client=_ok()).startswith("错误：")


# ---------- 参数 ----------


def test_missing_query_is_rejected_without_calling_the_api():
    called = []

    def handler(request):  # pragma: no cover - 断言它不被调用
        called.append(request)
        return httpx.Response(200, json=_payload())

    result = web_search({}, client=_client(handler))

    assert result.startswith("错误：")
    assert "query" in result
    assert called == [], "参数错误不该产生一次网络请求（也不该计费）"


def test_request_carries_query_limit_and_bearer_token():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_payload())

    web_search({"query": "Avid agent runtime", "max_results": 3}, client=_client(handler))

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url == "https://api.tavily.com/search"
    assert request.headers["Authorization"] == "Bearer tvly-test-key"
    body = json.loads(request.content)
    assert body == {
        "query": "Avid agent runtime",
        "max_results": 3,
        "search_depth": "basic",
    }


@pytest.mark.parametrize(
    ("given", "expected"),
    [(0, 1), (-3, 1), (99, 20), ("7", 7), (None, 5), ("abc", 5)],
)
def test_max_results_is_clamped_into_the_tavily_range(given, expected):
    """越界值在本地收口：Tavily 对越界回 422，那会把"少要几条"变成整个调用失败。"""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_payload())

    web_search({"query": "q", "max_results": given}, client=_client(handler))

    assert json.loads(seen[0].content)["max_results"] == expected


# ---------- 成功路径 ----------


def test_results_are_rendered_as_a_readable_list():
    result = web_search({"query": "Avid agent runtime"}, client=_ok())

    assert result.startswith("检索「Avid agent runtime」：共 2 条结果")
    assert "1. Avid 项目" in result
    assert "https://example.com/avid" in result
    assert "Avid 是一个自建 agent 运行时。" in result
    assert "2. 另一个来源" in result


def test_the_answer_field_is_included_when_present():
    result = web_search({"query": "q"}, client=_ok(answer="这是直接回答"))

    assert "摘答：这是直接回答" in result


def test_snippets_are_single_line_and_capped():
    long_text = "第一行\n第二行 " + "x" * (MAX_SNIPPET_CHARS * 2)
    result = web_search(
        {"query": "q"},
        client=_ok(results=[{"title": "t", "url": "u", "content": long_text}]),
    )

    assert "摘要已截断" in result
    assert "\n第二行" not in result  # 换行被压成空格，编号不会被顶掉
    snippet_line = next(line for line in result.splitlines() if line.startswith("   ") and "x" in line)
    assert len(snippet_line.strip()) <= MAX_SNIPPET_CHARS + len("…（摘要已截断）")


def test_empty_results_are_not_reported_as_an_error():
    """查到了、只是没有匹配：这是正常结果，不该让模型以为工具坏了。"""
    result = web_search({"query": "很冷门的词"}, client=_ok(results=[]))

    assert not result.startswith("错误：")
    assert "共 0 条结果" in result
    assert "没有匹配的网页" in result


def test_non_dict_response_body_is_handled():
    result = web_search(
        {"query": "q"}, client=_client(lambda request: httpx.Response(200, json=[1, 2]))
    )

    assert result.startswith("错误：")
    assert "无法解析" in result


def test_non_json_success_body_is_handled():
    result = web_search(
        {"query": "q"},
        client=_client(lambda request: httpx.Response(200, text="<html>gateway</html>")),
    )

    assert result.startswith("错误：")
    assert "不是合法 JSON" in result


# ---------- 失败路径 ----------


@pytest.mark.parametrize("status", [400, 401, 429, 432, 433, 500])
def test_http_errors_surface_the_provider_message(status):
    def handler(request):
        return httpx.Response(status, json={"detail": {"error": "额度或鉴权有问题"}})

    result = web_search({"query": "q"}, client=_client(handler))

    assert result.startswith("错误：")
    assert f"HTTP {status}" in result
    assert "额度或鉴权有问题" in result
    assert "TAVILY_API_KEY" in result  # 给下一步
    assert "不要用相同查询重复调用" in result


def test_http_error_without_a_usable_envelope_falls_back_to_the_body():
    result = web_search(
        {"query": "q"},
        client=_client(lambda request: httpx.Response(500, text="Internal Server Error")),
    )

    assert "Internal Server Error" in result


def test_timeout_is_reported_as_a_transient_failure():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    result = web_search({"query": "q"}, client=_client(handler))

    assert result.startswith("错误：")
    assert "请求失败" in result
    assert "稍后重试" in result


def test_connection_error_is_reported_as_a_transient_failure():
    def handler(request):
        raise httpx.ConnectError("dns boom")

    result = web_search({"query": "q"}, client=_client(handler))

    assert result.startswith("错误：")
    assert "dns boom" in result


def test_failures_never_raise():
    """工具失败一律回文本：循环不该因为一次检索失败而中断（execution 的既有约定）。"""
    def broken(request):
        raise httpx.ReadError("boom")

    for handler in (
        broken,
        lambda request: httpx.Response(401, json={"detail": {"error": "no"}}),
        lambda request: httpx.Response(200, text="not json"),
    ):
        assert web_search({"query": "q"}, client=_client(handler)).startswith("错误：")
