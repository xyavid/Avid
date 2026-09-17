"""从环境变量读取并校验模型配置。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.openai.com/v1"

ENV_API_KEY = "AVID_API_KEY"
ENV_BASE_URL = "AVID_BASE_URL"
ENV_MODEL = "AVID_MODEL"

REQUIRED = (ENV_API_KEY, ENV_MODEL)


class ConfigError(Exception):
    """配置缺失或非法。错误信息自带修复方法。"""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    source = os.environ if env is None else env

    missing = [name for name in REQUIRED if not source.get(name, "").strip()]
    if missing:
        raise ConfigError(
            "缺少必需的环境变量：" + "、".join(missing) + "\n"
            "设置方法：cp .env.example .env 填入真实值，然后运行\n"
            '  uv run --env-file .env avid "你好"'
        )

    return Config(
        api_key=source[ENV_API_KEY].strip(),
        base_url=source.get(ENV_BASE_URL, "").strip() or DEFAULT_BASE_URL,
        model=source[ENV_MODEL].strip(),
    )
