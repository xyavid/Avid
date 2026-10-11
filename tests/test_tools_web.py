"""web_search / web_fetch: request shape, the failure taxonomy, the address guardrail, and the
rules that keep a page's navigation out of the model's context.

All HTTP goes through ``httpx.MockTransport``; nothing here touches the network or DNS.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from avid import web_settings
from avid.agent.tools import web
from avid.agent.tools.extract import looks_like_shell

FIXTURES = Path(__file__).parent / "fixtures" / "html"

#: A literal public address: no DNS, and ``is_global`` lets it through the guardrail.
PUBLIC_IP = "93.184.216.34"


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)


@pytest.fixture
def search_key(monkeypatch, tmp_path) -> Path:
    """The search key in the same store the model keys live in, read per call."""
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({web_settings.SECRET_REF: "tvly-test"}), encoding="utf-8")
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(path))
    return path


def search_body(**overrides) -> dict:
    body = {
        "results": [
            {"title": "First hit", "url": "https://example.com/a", "content": "Snippet one."},
            {"title": "Second hit", "url": "https://example.com/b", "content": "Snippet two."},
        ]
    }
    body.update(overrides)
    return body


def json_handler(body: dict, *, capture: list | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, json=body)

    return handler


# ---- search: configuration ----


def test_search_without_a_key_fails_closed_and_says_where_to_put_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(tmp_path / "secrets.json"))

    text = web.web_search({"query": "anything"})

    assert text.startswith("错误：")
    assert "secrets.json" in text
    assert web_settings.SECRET_REF in text
    assert web_settings.SIGNUP_URL in text


def test_search_uses_the_configured_endpoint_and_a_bearer_key(search_key, monkeypatch):
    captured: list[httpx.Request] = []
    monkeypatch.setenv(web_settings.ENV_SEARCH_URL, "https://search.example/v2")

    text = web.web_search({"query": "  how to parse html  "}, client=mock_client(json_handler(search_body(), capture=captured)))

    request = captured[0]
    assert str(request.url) == "https://search.example/v2/search"
    assert request.headers["authorization"] == "Bearer tvly-test"
    payload = json.loads(request.content)
    assert payload["query"] == "how to parse html"
    assert payload["max_results"] == web.DEFAULT_MAX_RESULTS
    assert "First hit" in text and "https://example.com/a" in text


def test_search_defaults_to_the_built_in_endpoint(search_key):
    captured: list[httpx.Request] = []

    web.web_search({"query": "x"}, client=mock_client(json_handler(search_body(), capture=captured)))

    assert str(captured[0].url) == f"{web_settings.DEFAULT_SEARCH_URL}/search"


@pytest.mark.parametrize(("asked", "expected"), [(0, 1), (-3, 1), (999, web.MAX_RESULTS_CEILING)])
def test_search_clamps_max_results(search_key, asked: int, expected: int):
    captured: list[httpx.Request] = []

    web.web_search(
        {"query": "x", "max_results": asked}, client=mock_client(json_handler(search_body(), capture=captured))
    )

    assert json.loads(captured[0].content)["max_results"] == expected


def test_search_rejects_a_non_ascii_key(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({web_settings.SECRET_REF: "tvly-键"}), encoding="utf-8")
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(path))

    text = web.web_search({"query": "x"})

    assert "非 ASCII" in text
    assert web_settings.SECRET_REF in text


def test_search_without_a_query_is_a_parameter_error(search_key):
    assert web.web_search({"query": "   "}).startswith("错误：")


# ---- search: rendering ----


def test_search_renders_the_providers_own_answer_when_present(search_key):
    text = web.web_search(
        {"query": "x"}, client=mock_client(json_handler(search_body(answer="A direct answer.")))
    )

    assert "摘答：A direct answer." in text


def test_search_says_an_empty_result_is_not_a_failure(search_key):
    text = web.web_search({"query": "x"}, client=mock_client(json_handler({"results": []})))

    assert "没有匹配的网页" in text
    assert not text.startswith("错误：")


def test_search_collapses_and_truncates_snippets(search_key):
    long_line = "word " * 400
    body = {"results": [{"title": "t", "url": "u", "content": f"line one\nline two {long_line}"}]}

    text = web.web_search({"query": "x"}, client=mock_client(json_handler(body)))

    assert "line one line two" in text
    assert "摘要已截断" in text


# ---- search: failure taxonomy ----


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_search_reports_provider_status_codes(search_key, status: int):
    handler = lambda request: httpx.Response(status, json={"detail": {"error": "nope"}})  # noqa: E731

    text = web.web_search({"query": "x"}, client=mock_client(handler))

    assert text.startswith("错误：")
    assert str(status) in text
    assert "重复调用" in text


def test_search_reports_a_non_json_response(search_key):
    handler = lambda request: httpx.Response(200, text="<html>not json</html>")  # noqa: E731

    assert "JSON" in web.web_search({"query": "x"}, client=mock_client(handler))


def test_search_reports_a_json_response_that_is_not_an_object(search_key):
    handler = lambda request: httpx.Response(200, json=[1, 2, 3])  # noqa: E731

    assert web.web_search({"query": "x"}, client=mock_client(handler)).startswith("错误：")


def test_search_reports_a_timeout(search_key):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    text = web.web_search({"query": "x"}, client=mock_client(handler))

    assert text.startswith("错误：")
    assert "网络" in text or "超时" in text


def test_search_reports_a_connection_failure(search_key):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert web.web_search({"query": "x"}, client=mock_client(handler)).startswith("错误：")


# ---- fetch: the address guardrail ----


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "https:///no-host",
    ],
)
def test_fetch_refuses_anything_but_http_and_https(url: str):
    text = web.web_fetch({"url": url})

    assert text.startswith("错误：")
    assert "http" in text


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://127.0.0.1:8000/admin", "loopback"),
        ("http://localhost:8000/", "loopback"),
        ("http://10.1.2.3/internal", "private"),
        ("http://192.168.0.1/router", "private"),
        ("http://172.16.5.4/", "private"),
        ("http://169.254.169.254/latest/meta-data/", "link-local"),
        ("http://[::1]:8000/", "loopback"),
    ],
)
def test_fetch_refuses_addresses_that_are_not_public(url: str, why: str):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("a refused address must not be requested")

    text = web.web_fetch({"url": url}, client=mock_client(handler))

    assert text.startswith("错误：")
    assert "拒" in text


def test_fetch_follows_a_redirect_but_rechecks_every_hop(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/secret"})

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/start"}, client=mock_client(handler))

    assert text.startswith("错误：")
    assert "拒" in text


def test_fetch_stops_after_the_redirect_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"http://{PUBLIC_IP}/next"})

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/start"}, client=mock_client(handler))

    assert text.startswith("错误：")
    assert "重定向" in text


def test_fetch_can_be_opted_in_to_private_addresses(monkeypatch, search_key):
    """An internal wiki on a private address is a real case, so the guardrail is switchable."""
    monkeypatch.setenv(web_settings.ENV_ALLOW_PRIVATE, "1")
    html = (FIXTURES / "main-article.html").read_text(encoding="utf-8")
    handler = lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})  # noqa: E731

    text = web.web_fetch({"url": "http://127.0.0.1:8000/docs"}, client=mock_client(handler))

    assert "Fetching returns the decoded body" in text


# ---- fetch: content rules ----


def test_fetch_refuses_non_text_content(search_key):
    handler = lambda request: httpx.Response(  # noqa: E731
        200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
    )

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/doc.pdf"}, client=mock_client(handler))

    assert text.startswith("错误：")
    assert "application/pdf" in text


def test_fetch_extracts_prose_and_reports_its_provenance(search_key):
    html = (FIXTURES / "chrome-heavy-article.html").read_text(encoding="utf-8")
    handler = lambda request: httpx.Response(  # noqa: E731
        200,
        text=html,
        headers={"content-type": "text/html; charset=utf-8"},
    )

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/wiki/Web_scraping"}, client=mock_client(handler))

    assert "Web scraping, web harvesting, or web data extraction" in text
    assert "Random article" not in text
    assert "text/html" in text
    assert "utf-8" in text


def test_fetch_passes_plain_text_through_without_html_extraction(search_key):
    handler = lambda request: httpx.Response(  # noqa: E731
        200, text="raw <not> html", headers={"content-type": "text/plain; charset=utf-8"}
    )

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/robots.txt"}, client=mock_client(handler))

    assert "raw <not> html" in text


def test_fetch_flags_a_page_whose_body_is_rendered_elsewhere(search_key):
    html = (FIXTURES / "js-shell.html").read_text(encoding="utf-8")
    handler = lambda request: httpx.Response(  # noqa: E731
        200, text=html, headers={"content-type": "text/html"}
    )

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/app"}, client=mock_client(handler))

    assert looks_like_shell("", len(html)) is True  # the fixture really is below the floor
    assert "JavaScript" in text


def test_fetch_truncates_an_oversized_body_with_a_notice(search_key):
    blocks = "".join(f"<p>paragraph {i} " + ("sentence " * 400) + "</p>" for i in range(200))
    html = f"<html><body>{blocks}</body></html>"  # far past MAX_FETCH_CHARS
    handler = lambda request: httpx.Response(  # noqa: E731
        200, text=html, headers={"content-type": "text/html"}
    )

    text = web.web_fetch({"url": f"http://{PUBLIC_IP}/big"}, client=mock_client(handler))

    assert len(text) < web.MAX_FETCH_CHARS + 2000
    assert "截断" in text


def test_fetch_reports_a_missing_url_and_a_network_failure(search_key):
    assert web.web_fetch({"url": "  "}).startswith("错误：")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert web.web_fetch({"url": f"http://{PUBLIC_IP}/x"}, client=mock_client(handler)).startswith("错误：")


# ---- registration ----


def test_both_tools_are_registered_read_only_and_parallel():
    from avid.agent.tools import registry
    from avid.agent.tools.safety import CONCURRENCY_SAFE

    specs = {spec.name: spec for spec in registry.specs()}
    for name in ("web_search", "web_fetch"):
        assert name in specs
        assert specs[name].writes is False
        assert name in CONCURRENCY_SAFE
