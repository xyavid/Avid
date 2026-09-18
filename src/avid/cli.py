"""命令行入口：avid "问题"

会话（阶段 12）在 CLI 里是这样接线的：

* ``--session ID``：续接该会话；不存在就创建。历史从会话读回，本轮的新消息
  由 ``agent_loop`` 的 ``on_message`` 观察点逐条落库。
* ``--new-session``：新建会话并把 id 打到 stderr（stdout 只放模型的回答）。
* ``--list-sessions`` / ``--delete-session``：会话的查看与销毁。

CLI 是**唯一**同时认识 ``runtime`` 与 ``session`` 的地方：循环不认识持久化，
会话包也不认识运行时（不变量 I7）。

会话落盘在 ``工作区/.avid/sessions/``。``--list-sessions`` 会打开每个会话读名字
与条数（比 pi 的"只读 header"贵），代价是 O(文件大小)×会话数——等这个代价在
真实使用里变得可感时，再把名字冗余进 header。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .runtime.loop import RoundLimitExceeded, agent_loop
from .ai.config import ConfigError, load_config
from .ai.client import LLMError, ask
from .policy.permission import DEFAULT_MODE, MODE_LABELS, MODES
from .session import (
    JsonlSessionRepo,
    JsonlSessionMetadata,
    SessionError,
    SessionRecorder,
    messages_for_branch,
)
from .tools import TOOLS, workspace

SESSION_DIR = ".avid/sessions"

# 工具清单从注册表派生：硬编码过两次，两次都漏（写 8 个时实际已有 14 个）。
# cli 已经是「认识 tools 包」的接线处，这里不新增模块边。
AGENT_TOOL_HELP = "（" + " / ".join(item["function"]["name"] for item in TOOLS) + "）"


def _session_root() -> Path:
    """会话目录固定在工作区内：与阶段 8 的落盘同一条理由——工具要能读回来。"""
    return Path(workspace.WORKSPACE_ROOT) / SESSION_DIR


def _local_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avid",
        description="向模型发一次提问，打印回复与 token 用量",
    )
    parser.add_argument("prompt", nargs="?", help="要发送给模型的问题")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="走 agent 循环，模型可调用已注册的工具" + AGENT_TOOL_HELP,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过审批闸门（硬拒绝仍然生效），非交互场景需显式指定",
    )
    parser.add_argument(
        "--permission",
        choices=MODES,
        default=DEFAULT_MODE,
        metavar="{strict,workspace,system}",
        help="权限模式："
        + "；".join(f"{mode}={MODE_LABELS[mode]}" for mode in MODES)
        + f"（默认 {DEFAULT_MODE}）。模式决定哪些动作要问，--yes 只决定谁来回答",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help="续接指定会话（不存在就创建），隐含 --agent",
    )
    parser.add_argument(
        "--new-session",
        action="store_true",
        help="新建一个会话并打印 id，隐含 --agent",
    )
    parser.add_argument(
        "--session-name",
        metavar="NAME",
        help="与 --session / --new-session 一起用：设置会话名",
    )
    parser.add_argument(
        "--list-sessions", action="store_true", help="列出会话后退出"
    )
    parser.add_argument(
        "--delete-session", metavar="ID", help="删除一个已关闭的会话后退出"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 子命令 `avid web` 在普通解析之前分流：既有的 `avid "问题"` 逐字不变。
    if argv and argv[0] == "web":
        return _run_web(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_sessions or args.delete_session is not None:
        return _session_admin(args)
    if args.session and args.new_session:
        parser.error("--session 与 --new-session 只能选一个")
    if args.session_name and not (args.session or args.new_session):
        parser.error("--session-name 需要与 --session 或 --new-session 一起用")
    if not args.prompt:
        parser.error("缺少要发送的内容")

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.session or args.new_session:
        args.agent = True

    if args.agent:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)  # 只留自己的 trace 行
        if args.session or args.new_session:
            return _run_session(args, config)
        try:
            print(
                agent_loop(
                    [{"role": "user", "content": args.prompt}],
                    config=config,
                    auto_approve=args.yes,
                    permission_mode=args.permission,
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


def _run_session(args: argparse.Namespace, config) -> int:
    """续接或新建会话跑一次循环：历史来自会话，本轮消息逐条写回会话。"""
    repo = JsonlSessionRepo(_session_root())
    session = None
    created = False
    try:
        if args.new_session:
            session = repo.create()
            created = True
        else:
            existing = _find(repo, args.session)
            if existing is None:
                session = repo.create(id=args.session)
                created = True
            else:
                session = repo.open(existing)
        if args.session_name:
            session.set_name(args.session_name)

        recorder = SessionRecorder(session)
        history = messages_for_branch(session, recorder.branch)
        recorder.ensure_branch()
        messages = [*history, {"role": "user", "content": args.prompt}]
        try:
            reply = agent_loop(
                messages,
                config=config,
                auto_approve=args.yes,
                permission_mode=args.permission,
                on_message=recorder.on_message,
            )
        except (LLMError, RoundLimitExceeded) as exc:
            print(f"循环中止：{exc}", file=sys.stderr)
            return 1
        stats = session.get_stats()
    except SessionError as exc:
        print(f"会话错误：{exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None and not session.closed:
            session.close()
        repo.close()

    print(reply)
    print(
        f"--- session={session.metadata.id}"
        f" {'新建' if created else '续接'}"
        f" messages={stats.message_count} ---",
        file=sys.stderr,
    )
    return 0


def _session_admin(args: argparse.Namespace) -> int:
    """查看与销毁：都不调模型，所以不需要先校验模型配置。"""
    repo = JsonlSessionRepo(_session_root())
    try:
        if args.delete_session is not None:
            found = _find(repo, args.delete_session)
            if found is None:
                print(f"没有这个会话：{args.delete_session}", file=sys.stderr)
                return 1
            repo.delete(found)
            print(f"已删除会话 {found.id}", file=sys.stderr)
            return 0

        items = repo.list()
        if not items:
            print("（还没有会话）")
            return 0
        for meta in items:
            name, count = _peek(repo, meta)
            print(
                f"{meta.id}\t{_local_time(meta.created_at)}\t{count}\t{name or '-'}"
            )
        return 0
    except SessionError as exc:
        print(f"会话错误：{exc}", file=sys.stderr)
        return 1
    finally:
        repo.close()


def _find(repo: JsonlSessionRepo, session_id: str) -> JsonlSessionMetadata | None:
    for meta in repo.list():
        if meta.id == session_id:
            return meta
    return None


def build_web_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avid web",
        description="起本地 Web 服务：REST + SSE 事件流 + 已构建的静态资源",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认回环）")
    parser.add_argument(
        "--port", type=int, default=8765, help="监听端口（默认 8765）"
    )
    parser.add_argument(
        "--reload", action="store_true", help="开发模式：代码变更自动重载"
    )
    return parser


def _run_web(argv: list[str]) -> int:
    """``avid web``：起 uvicorn。

    Web 依赖是**可选的**（``pyproject.toml`` 的 ``[project.optional-dependencies].web``），
    所以缺依赖时给出可执行的修复命令，而不是 ImportError 栈。
    """
    args = build_web_parser().parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print(
            "缺少 Web 依赖。安装方法：uv sync --extra web\n"
            "（或 uv run --extra web avid web）",
            file=sys.stderr,
        )
        return 2

    from .web import create_app

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(
        f"Avid Web 正在监听 http://{args.host}:{args.port}\n"
        "开发期前端：pnpm -C web dev（Vite 代理 /api → 本进程）",
        file=sys.stderr,
    )
    if args.reload:
        uvicorn.run(
            "avid.web:create_app", factory=True, host=args.host, port=args.port, reload=True
        )
    else:
        uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def _peek(repo: JsonlSessionRepo, meta: JsonlSessionMetadata) -> tuple[str | None, int]:
    """打开看一眼名字与条数。读不了就当没有名字——列表不该因为一个坏文件失败。"""
    try:
        session = repo.open(meta)
    except SessionError:
        return None, 0
    try:
        return session.get_name(), session.get_stats().message_count
    finally:
        session.close()
