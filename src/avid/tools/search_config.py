"""联网检索的配置：从环境变量读取 Tavily 凭据。

与 ``ai/config.py`` 同形（常量 + ``load_*`` 函数 + 自带修复方法的异常），但**刻意独立**：
联网检索是可选能力，没有 Key 只该让 ``web_search`` 失败关闭，不该拖累模块调用、会话、
Web 服务这些主流程——所以它不进 ``Config``、也不参与 ``load_config()`` 的必填校验。

**调用时读取，不在 import 时**：这样同进程里改环境变量（测试的 ``monkeypatch.setenv``、
Web 服务运行期换 Key）立刻生效，而不是被冻结在 import 那一刻。

密钥只从环境变量来，不写进任何文件、不进日志、不回显到工具结果里。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

#: Tavily 控制台签发的 Key（形如 ``tvly-…``）。
ENV_API_KEY = "TAVILY_API_KEY"
#: 可选：接口根地址。留出这个口子是为了测试注入与自建/代理端点，
#: 默认值是唯一被硬编码的地址，且只此一处。
ENV_BASE_URL = "TAVILY_BASE_URL"

DEFAULT_BASE_URL = "https://api.tavily.com"


class SearchConfigError(Exception):
    """联网检索配置缺失或非法。错误信息自带修复方法（与 ``ConfigError`` 同一约定）。"""


@dataclass(frozen=True)
class SearchConfig:
    api_key: str
    base_url: str

    @property
    def search_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/search"


def load_search_config(env: Mapping[str, str] | None = None) -> SearchConfig:
    """读并校验检索配置；缺 Key 抛 :class:`SearchConfigError`。

    ``env`` 显式传入时为唯一来源（测试用），否则读 ``os.environ``。
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

    # Key 必须是 ASCII：非 ASCII 会让 httpx 在装请求头时抛 UnicodeEncodeError，那被
    # execution 的兜底收敛成「工具执行失败」——错误信息指向工具，根因却在配置。
    # 最常见的来路是 .env 里跟在 Key 后面的中文注释（`TAVILY_API_KEY=tvly-x  # 注释`
    # 会被整行当成值，注释不是行首所以不算注释）。
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
