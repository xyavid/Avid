"""命令行入口：avid "问题"

会话（阶段 12）在 CLI 里是这样接线的：

* ``--session ID``：续接该会话；不存在就创建。历史从会话读回，本轮的新消息
  由 ``agent_loop`` 的 ``on_message`` 观察点逐条落库。
* ``--new-session``：新建会话并把 id 打到 stderr（stdout 只放模型的回答）。
* ``--list-sessions`` / ``--delete-session``：会话的查看与销毁。

CLI 是**唯一**同时认识 ``runtime`` 与 ``session`` 的地方：循环不认识持久化，
会话包也不认识运行时（不变量 I7）。

会话落盘在 ``工作区/.avid/sessions/``。``--list-sessions`` 的名字与条数来自
``JsonlSessionRepo.summarize``：读一次文件 + 解析尾部窗口，不重放整个会话
（以前是 O(文件大小)×会话数，见 `session/jsonl.py` 的 `summarize_file`）。
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from datetime import datetime
from pathlib import Path

from .ai.client import LLMError, ask
from .ai.config import ConfigError, load_config
from .policy.permission import DEFAULT_MODE, MODE_LABELS, MODES
from .runtime.loop import RoundLimitExceeded, agent_loop
from .session import (
    JsonlSessionMetadata,
    JsonlSessionRepo,
    SessionError,
    SessionRecorder,
    messages_for_branch,
)
from .svc.workspaces import WorkspaceInvalid, bound_workspace
from .tools import TOOLS, workspace
from .web.app import LOOPBACK_HOSTS, trusted_hosts
from .workspaces import (
    Workspace,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspaceRegistry,
    sessions_root,
)

# 工具清单从注册表派生：硬编码过两次，两次都漏（写 8 个时实际已有 14 个）。
# cli 已经是「认识 tools 包」的接线处，这里不新增模块边。
AGENT_TOOL_HELP = "（" + " / ".join(item["function"]["name"] for item in TOOLS) + "）"


def _local_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _resolve_workspace(selection: str | None) -> Workspace:
    """解析这次命令用哪个工作区。**只读**——注册表只由 `avid workspace` 写。

    ``--workspace`` 可以给已登记的 id/路径，也可以直接给一个目录（那就是这个进程的
    工作地点，当场生效但不登记）。缺省是**当前目录**——命令行上下文替你选了它，
    不是"没选"；解析结果会打印，所以不存在"归属不明"的会话。
    """
    registry = WorkspaceRegistry()
    if selection:
        found = registry.find(selection)
        if found is not None:
            return found
        try:
            return bound_workspace(selection)
        except WorkspaceInvalid as exc:
            raise WorkspaceNotFound(
                f"没有这个工作区：{selection}（{exc}；"
                "用 `avid workspace list` 看已登记的，或直接给一个存在的目录）"
            ) from exc

    root = Path(workspace.WORKSPACE_ROOT)
    found = registry.find(str(root))
    return found if found is not None else bound_workspace(root)


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
        default=None,
        metavar="{strict,workspace,system}",
        help="权限模式："
        + "；".join(f"{mode}={MODE_LABELS[mode]}" for mode in MODES)
        + "。缺省按「工作区默认权限」，工作区没设过就是 "
        + DEFAULT_MODE
        + "。模式决定哪些动作要问，--yes 只决定谁来回答",
    )
    parser.add_argument(
        "--workspace",
        metavar="PATH|ID",
        help="这次会话属于哪个工作区：可以给路径或已登记的 id；"
        "缺省是当前目录（会打印解析结果）。",
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
    # 子命令 `avid web` / `avid workspace` 在普通解析之前分流：
    # 既有的 `avid "问题"` 逐字不变。
    if argv and argv[0] == "web":
        return _run_web(argv[1:])
    if argv and argv[0] == "workspace":
        return _run_workspace(argv[1:])

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
    try:
        target = _resolve_workspace(args.workspace)
    except WorkspaceNotFound as exc:
        print(f"工作区错误：{exc}", file=sys.stderr)
        return 2
    print(
        f"工作区 {target.id}（{target.root}，默认权限 {target.default_permission}）",
        file=sys.stderr,
    )
    repo = JsonlSessionRepo(sessions_root(target), workspace=target.id)
    session = None
    created = False
    try:
        if args.new_session:
            session = repo.create(workspace=target.id)
            created = True
        else:
            existing = _find(repo, args.session)
            if existing is None:
                session = repo.create(id=args.session, workspace=target.id)
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
                permission_mode=args.permission or target.default_permission,
                workspace_root=target.root,
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
    try:
        target = _resolve_workspace(args.workspace)
    except WorkspaceNotFound as exc:
        print(f"工作区错误：{exc}", file=sys.stderr)
        return 2
    repo = JsonlSessionRepo(sessions_root(target), workspace=target.id)
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
                f"{meta.id}\t{_local_time(meta.created_at)}\t{count}"
                f"\t{meta.workspace or '-'}\t{name or '-'}"
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


def build_workspace_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avid workspace",
        description="管理已知的工作区：登记、列举、摘除、设置默认权限",
    )
    actions = parser.add_subparsers(dest="action", required=True)

    add = actions.add_parser("add", help="登记一个目录（同一个目录重复登记是幂等的）")
    add.add_argument("path", help="工作区目录")
    add.add_argument("--name", help="显示名（缺省用目录名）")
    add.add_argument(
        "--permission", choices=MODES, help="这个工作区的默认权限模式"
    )

    actions.add_parser("list", help="按最近使用列出已登记的工作区")

    remove = actions.add_parser("remove", help="从索引里摘掉（不动磁盘上的会话数据）")
    remove.add_argument("workspace", help="工作区 id 或路径")

    permission = actions.add_parser("permission", help="设置工作区的默认权限")
    permission.add_argument("workspace", help="工作区 id 或路径")
    permission.add_argument("mode", choices=MODES)
    return parser


def _run_workspace(argv: list[str]) -> int:
    """``avid workspace``：注册表的唯一写入口。会话数据一律不在这里动。"""
    args = build_workspace_parser().parse_args(argv)
    registry = WorkspaceRegistry()
    try:
        if args.action == "add":
            before = registry.find(args.path)
            ws = registry.add(args.path, name=args.name, permission=args.permission)
            if before is not None:
                print(f"已登记过，未重复添加：{ws.id}\t{ws.root}\t{ws.name}")
            else:
                print(f"{ws.id}\t{ws.root}\t{ws.name}\t{ws.default_permission}")
            return 0

        if args.action == "list":
            items = registry.list()
            if not items:
                print("（还没有登记任何工作区；用 `avid workspace add <路径>` 登记）")
                return 0
            for ws in items:
                print(
                    f"{ws.id}\t{ws.root}\t{ws.default_permission}\t{ws.name}"
                    f"\t{_local_time(ws.last_used_at)}"
                )
            return 0

        if args.action == "remove":
            ws = registry.remove(args.workspace)
            print(f"已从索引里摘掉 {ws.id}（{ws.root}）；磁盘上的会话数据未动")
            return 0

        ws = registry.set_permission(args.workspace, args.mode)
        print(f"{ws.id}\t{ws.default_permission}")
        return 0
    except WorkspaceError as exc:
        print(f"工作区错误：{exc}", file=sys.stderr)
        return 1


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
        "--workspace",
        metavar="PATH|ID",
        help="把这个进程绑到一个工作区（单工作区模式）：建会话可以省略 workspace。"
        "缺省是多工作区模式，候选来自 `avid workspace list`，建会话必须指定归属。",
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
    allowed_hosts = _allowed_hosts(args.host)
    print(
        f"Avid Web 正在监听 http://{args.host}:{args.port}\n"
        "开发期前端：pnpm -C web dev（Vite 代理 /api → 本进程）",
        file=sys.stderr,
    )
    if args.reload:
        if args.workspace:
            print(
                "注意：--reload 走模块工厂，--workspace 在重载模式下不生效；"
                "要绑定工作区请去掉 --reload。",
                file=sys.stderr,
            )
        uvicorn.run(
            "avid.web:create_app", factory=True, host=args.host, port=args.port, reload=True
        )
    else:
        uvicorn.run(
            create_app(workspace_root=args.workspace, allowed_hosts=allowed_hosts),
            host=args.host,
            port=args.port,
        )
    return 0


def _allowed_hosts(host: str) -> frozenset[str]:
    """这次监听允许哪些 Host / Origin 主机名（信任边界，见 `web/app.py`）。

    回环之外要显式放行：绑 `0.0.0.0` 时用户通常用本机 IP 访问，所以把本机地址也
    加进来，并打印一条警告——那不是"只在本地"了。`AVID_ALLOWED_HOSTS`（逗号分隔）
    是给反向代理/自定义域名的逃生口。
    """
    if host in LOOPBACK_HOSTS:
        return trusted_hosts()
    print(
        f"⚠ 正在监听非回环地址 {host}：本机其它用户与局域网都能访问这个进程。"
        "\n  Host/Origin 白名单已加入本机地址；需要额外域名请设 AVID_ALLOWED_HOSTS。",
        file=sys.stderr,
    )
    extra = {_host_of(host)}
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        extra.update(addresses)
    except OSError:  # pragma: no cover - 取不到本机地址时只信显式给的那些
        pass
    return trusted_hosts(frozenset(item for item in extra if item))


def _host_of(value: str) -> str:
    text = (value or "").strip().lower()
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    return text.rsplit(":", 1)[0]


def _peek(repo: JsonlSessionRepo, meta: JsonlSessionMetadata) -> tuple[str | None, int]:
    """名字与条数：走**不重放**的摘要路径。

    以前这里 `open()` 整个会话只为读这两个数（代价 O(文件大小)×会话数，见文件头
    的旧注释）；现在只读一次文件、解析尾部窗口，并按 (mtime,size) 缓存。
    读不了就当没有名字——列表不该因为一个坏文件失败。
    """
    try:
        summary = repo.summarize(meta)
    except SessionError:
        return None, 0
    return summary.name, summary.message_count
