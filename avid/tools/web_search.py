"""web_search: runs a query against the public web and returns titles, links and snippets.

The model alone judges whether a question needs the web, and the provider's field names stay
in this module.

"""


from __future__ import annotations

from typing import Any

import httpx

from .registry import tool
from .search_config import SearchConfig, SearchConfigError, load_search_config

#: HTTP client timeout: connecting is quicker, the whole exchange is given room.
TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 10.0

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_CEILING = 20
#: Per-snippet character cap, since advanced results can otherwise turn one search into
#: thousands of tokens.
MAX_SNIPPET_CHARS = 800
#: Cap on the whole formatted result, a backstop against a flooding response.
MAX_OUTPUT_CHARS = 12000


def _limited(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    """
    Clamps a numeric argument, since a direct call bypasses the schema's own bounds.

    The provider rejects an out-of-range value, turning a wrong count into a failed call.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def _snippet(text: Any) -> str:
    """
    Collapses a snippet to one line and truncates it.

    Embedded newlines would push the list numbering to a line start, reading as a new result.
    """
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= MAX_SNIPPET_CHARS:
        return collapsed
    return collapsed[:MAX_SNIPPET_CHARS] + "…（摘要已截断）"


def _error_text(response: httpx.Response) -> str:
    """Extracts a readable message from an error response, falling back to the body text."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]

    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("error"):
        return str(detail["error"])[:300]
    if isinstance(detail, list) and detail:
        return str(detail[0])[:300]
    return response.text[:300]


def _search_request(
    config: SearchConfig,
    query: str,
    *,
    max_results: int,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """
    Sends one search request and returns the parsed response body.

    The external protocol adapter, where fields, authentication and error shapes follow the
    provider.
    """
    payload = {
        "query": query,
        "max_results": max_results,
        # Basic depth by default: advanced costs more and is slower, and a better query is the
        # model's call rather than something to spend on here.
        "search_depth": "basic",
    }
    owned = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    )
    try:
        response = http.post(
            config.search_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code} — {_error_text(response)}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"响应不是合法 JSON：{response.text[:200]}") from exc
    finally:
        if owned:
            http.close()


def _format(query: str, data: Any) -> str:
    """Renders a response body as the plain-text list handed back to the model."""
    if not isinstance(data, dict):
        return f"错误：检索「{query}」的响应不是对象，无法解析；请换一种问法或稍后重试。"

    raw_results = data.get("results")
    results = [item for item in (raw_results or []) if isinstance(item, dict)]

    header = f"检索「{query}」：共 {len(results)} 条结果"
    if not results:
        return (
            header
            + "\n没有匹配的网页。请换更具体或更宽泛的关键词重试，"
            "不要用完全相同的查询重复调用。"
        )

    lines: list[str] = [header]
    answer = data.get("answer")
    if isinstance(answer, str) and answer.strip():
        # The provider's own answer, when requested, and the only conclusion rather than a
        # fragment.
        lines.append(f"摘答：{' '.join(answer.split())}")

    for index, item in enumerate(results, start=1):
        lines.append(
            f"{index}. {_snippet(item.get('title'))}\n"
            f"   {_snippet(item.get('url'))}\n"
            f"   {_snippet(item.get('content'))}"
        )

    lines.append(
        "以上摘要只是网页片段，引用前请点开链接确认；需要网页全文请用 bash 取回后读取。"
    )
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n…（结果过长已截断，共 {len(results)} 条）"
    return text


@tool(
    name="web_search",
    description="检索公开网页并返回「标题 / 链接 / 摘要」清单。"
    "【只在判断需要联网才能拿到信息时调用】：涉及最新动态、外部事实、别人写的文档或"
    "本机代码里没有的答案时才用；能从工作区文件、既有上下文或自己的知识得出答案的，"
    "不要调用。检索服务来自 Tavily，需要设置 TAVILY_API_KEY，"
    "没配置或调用失败时会返回一句「错误：」说明与下一步。"
    "结果是网页片段而非全文：需要正文时用 bash 取回后再读，引用前先核对链接。",
    properties={
        "query": {
            "type": "string",
            "description": "要检索的查询语句，用自然语言写清要找什么；越具体结果越准。",
        },
        "max_results": {
            "type": "integer",
            "description": "期望返回的结果条数，默认 5，上限 20；条数越多上下文越长。",
            "minimum": 1,
            "maximum": 20,
        },
    },
    required=("query",),
    # Network search: each call is an independent request.
    concurrency="safe",
)
def web_search(args: dict[str, Any], *, client: httpx.Client | None = None) -> str:
    """Searches the public web for ``args["query"]`` and returns a plain-text result list.

    ``client`` exists for tests and reuse, which inject a mock transport.
    """
    query = str(args.get("query", "")).strip()
    if not query:
        return "错误：缺少参数 query（要检索的查询语句）。"

    max_results = _limited(
        args.get("max_results"),
        default=DEFAULT_MAX_RESULTS,
        minimum=1,
        maximum=MAX_RESULTS_CEILING,
    )

    try:
        config = load_search_config()
    except SearchConfigError as exc:
        return f"错误：联网检索未配置。{exc}"

    try:
        data = _search_request(
            config, query, max_results=max_results, client=client
        )
    except httpx.HTTPError as exc:
        return (
            f"错误：联网检索请求失败（{exc}）；可能是网络不通或 Tavily 暂时不可用，"
            "请稍后重试或改用本地信息，不要用相同查询重复调用。"
        )
    except RuntimeError as exc:
        return (
            f"错误：联网检索失败（{exc}）；请检查 TAVILY_API_KEY 是否有效、额度是否用尽，"
            "或稍后重试，不要用相同查询重复调用。"
        )

    return _format(query, data)
