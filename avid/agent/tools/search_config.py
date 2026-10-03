"""Reads the Tavily credentials used by web search from the environment.

Kept apart from the main config so a missing key disables only search, and read per call so
a later key takes effect.

"""


from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

#: The key issued by the Tavily console, of the form ``tvly-...``.
ENV_API_KEY = "TAVILY_API_KEY"
#: Optional interface root, overridable for tests and for a self-hosted or proxy endpoint.
ENV_BASE_URL = "TAVILY_BASE_URL"

DEFAULT_BASE_URL = "https://api.tavily.com"


class SearchConfigError(Exception):
    """Missing or invalid search configuration; the message states how to fix it."""


@dataclass(frozen=True)
class SearchConfig:
    """One resolved search endpoint plus its credential."""

    api_key: str
    base_url: str

    @property
    def search_url(self) -> str:
        """The search endpoint, with any trailing slash on the base removed."""
        return f"{self.base_url.rstrip('/')}/search"


def load_search_config(env: Mapping[str, str] | None = None) -> SearchConfig:
    """Reads and validates the search configuration, raising when the key is absent.

    An explicit ``env`` is the only source when given, which is how tests supply one.
    """
    source = os.environ if env is None else env

    api_key = source.get(ENV_API_KEY, "").strip()
    if not api_key:
        raise SearchConfigError(
            f"缺少环境变量 {ENV_API_KEY}，无法联网检索。\n"
            "申请地址：https://app.tavily.com（免费额度即可）\n"
            "设置方法：把下面这行加进 .env，然后带 --env-file 运行\n"
            f"  {ENV_API_KEY}=tvly-你的真实Key\n"
            '  uv run --env-file .env avid --agent "你的问题"'
        )

    base_url = source.get(ENV_BASE_URL, "").strip() or DEFAULT_BASE_URL

    # A non-ASCII key would fail inside the HTTP client and be reported as a tool fault, so
    # it is rejected here with the usual cause: a trailing comment on the value in .env.
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SearchConfigError(
            f"{ENV_API_KEY} 含非 ASCII 字符，不是有效的 Key：{exc.reason}。\n"
            "常见原因：值后面跟了中文注释（.env 的注释必须独占一行，不能写在值后面）。\n"
            "请改成只放 Key 本身，例如：\n"
            f"  {ENV_API_KEY}=tvly-你的真实Key"
        ) from exc

    return SearchConfig(api_key=api_key, base_url=base_url)
