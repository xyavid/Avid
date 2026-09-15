"""命令行入口：avid "问题" """

from __future__ import annotations

import argparse
import logging
import sys

from .agent import RoundLimitExceeded, agent_loop
from .config import ConfigError, load_config
from .llm import LLMError, ask
from .permission import auto_approve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="avid",
        description="向模型发一次提问，打印回复与 token 用量",
    )
    parser.add_argument("prompt", help="要发送给模型的问题")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="走 agent 循环，模型可调用已注册的工具"
        "（bash / read_file / write_file / edit_file / glob）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过审批闸门（硬拒绝仍然生效），非交互场景需显式指定",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.agent:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)  # 只留自己的 trace 行
        try:
            print(
                agent_loop(
                    [{"role": "user", "content": args.prompt}],
                    config=config,
                    check=auto_approve if args.yes else None,
                )
            )
        except (LLMError, RoundLimitExceeded) as exc:
            print(f"循环中止：{exc}", file=sys.stderr)
            return 1
        return 0

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
