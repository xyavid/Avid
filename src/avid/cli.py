"""命令行入口：avid "问题" """

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config
from .llm import LLMError, ask


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="avid",
        description="向模型发一次提问，打印回复与 token 用量",
    )
    parser.add_argument("prompt", help="要发送给模型的问题")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    try:
        reply = ask(config, args.prompt)
    except LLMError as exc:
        print(f"调用失败：{exc}", file=sys.stderr)
        return 1

    print(reply.text)
    print(
        f"--- model={reply.model or config.model}"
        f" prompt={reply.usage.prompt_tokens}"
        f" completion={reply.usage.completion_tokens}"
        f" total={reply.usage.total_tokens} ---",
        file=sys.stderr,
    )
    return 0
