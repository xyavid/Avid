"""web_search：按查询检索公开网页，把「标题 / 链接 / 摘要」回传给模型。

**只在模型判断需要外部信息时才调用**——这条纪律写在工具描述里（模型看得到的地方），
因为运行时无从判断"这次问题是否真的需要联网"：误判成需要会白烧一次往返，误判成
不需要会答错，而只有模型手上有这个判断依据。

与本项目其余工具的三条一致约定：

* 失败一律**回文本、不抛异常**（``execution.execute_one`` 只兜底抛异常，那种文案说不出
  "该去设哪个环境变量"）。四类失败各给各的下一步：配置缺失（去设 Key）、鉴权/额度
  （别重试，去控制台）、网络与超时（可换查询或稍后）、响应不合约定（服务端问题）。
* **空结果不是失败**：查到了、只是没有匹配，回一句"没搜到"并给下一步，而不是报错。
* 与 ``glob`` / ``bash`` 一样返回**给人读的纯文本**，不是 JSON：模型的阅读成本更低，
  也让"摘要按上限截断"这件事有一个自然的落点。

Tavily 的字段名、错误信封与状态码语义只允许出现在本文件：外部协议的变化在这里被
适配掉，工具层以上的部分（注册表、执行环节、模型看到的 schema）不受影响。换供应商
时改这一个文件即可。

请求走 ``httpx``（已是内核依赖），**client 按调用点传入**而不是复用 ``ai/client.py``
的共享单例：那个客户端的超时与生命周期是按模型调用调的，检索不该被它绑定；显式传参
也正好是测试注入 MockTransport 的接缝。
"""

from __future__ import annotations

from typing import Any

import httpx

from .registry import tool
from .search_config import SearchConfig, SearchConfigError, load_search_config

#: Httpx 客户端超时（连接更快，整体留足）。
TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 10.0

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_CEILING = 20
#: 每条摘要的字符上限。Tavily 的 ``results[].content`` 在 advanced 深度下可以很长，
#: 不截断会把一次检索变成几千 token。
MAX_SNIPPET_CHARS = 800
#: 整份结果的字符上限：兜底防刷屏。超出时按上限截断并提示。
MAX_OUTPUT_CHARS = 12000


def _limited(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    """数值参数收口。

    schema 已经声明了 1–20 的边界（``tools/validate.py`` 会在调用前查一遍），这里再夹
    一次是因为直调路径（单测、复用某个工具）不经过校验，而 Tavily 对越界值回 422——
    失败方向从"少要几条结果"变成"整个调用失败"是不划算的。
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def _snippet(text: Any) -> str:
    """把摘要压成单行并截断。

    换行会被压成空格：多行摘要放进 "N. 标题 / URL / 摘要" 这种编号清单里会把编号
    顶到行首，模型会把摘要的某一行误读成新的结果条目。
    """
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= MAX_SNIPPET_CHARS:
        return collapsed
    return collapsed[:MAX_SNIPPET_CHARS] + "…（摘要已截断）"


def _error_text(response: httpx.Response) -> str:
    """从错误响应里取一句人话。

    Tavily 的错误信封是 ``{"detail": {"error": "..."}}``（422 是 ``detail`` 数组）。
    取不到就退回正文片段——总比只报状态码强。
    """
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
    """发一次检索请求，返回解析后的响应体。

    这是**外部协议适配点**：请求字段与鉴权方式按 Tavily 的约定在这里组装，越界值、
    超时、非 200、非 JSON 都在这里收敛成可读的中文异常，不泄漏到工具层。
    自建 client 归调用方所有（测试注入），不关它。
    """
    payload = {
        "query": query,
        "max_results": max_results,
        # 默认 basic：advanced 更贵（2 credits）也更慢，需要更高相关度时由模型改查询词
        # 或后续再加深度参数，不在这里替它花钱。
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
    """把响应体变成回给模型的正文（纯文本清单）。"""
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
        # Tavily 的 LLM 摘答（请求了 include_answer 才会有）：放最前面，它是唯一非片段的结论。
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
    # 出网检索；每次都独立请求，互不依赖。
    concurrency="safe",
)
def web_search(args: dict[str, Any], *, client: httpx.Client | None = None) -> str:
    """按 ``args["query"]`` 检索公开网页，返回纯文本结果清单。

    ``client`` 只给测试与复用方注入（``httpx.MockTransport``）；正常运行不传，
    由 :func:`_search_request` 按调用建一个。
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
