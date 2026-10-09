"""Command line entry point: one question, the agent loop, session resume, workspace admin and web."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from datetime import datetime
from pathlib import Path

from .agent import commands as commands_module
from .agent.checkpoints import DirCheckpointSink, restore, restore_tally, rewind_target
from .agent.run import Run
from .agent.spec import RunSpec
from .agent.state import RunState
from .agent.tools import TOOLS, build_toolset, workspace
from .agent.tools.mcp import McpManager
from .index import SessionIndexer, queries
from .index.indexer import notifying, workspace_lookup
from .providers.byok import resolve_chat
from .providers.client import LLMError, ask, chat_completion
from .providers.config import Config, ConfigError
from .providers.usage import Usage, hit_ratio
from .security import userdirs
from .security.permission import (
    PERMISSION_FULL,
    PERMISSION_NORMAL,
    RunSecurity,
)
from .services.session_migration import apply_migration, plan_migration
from .services.workspace_registry import (
    Workspace,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspaceRegistry,
    sessions_root,
)
from .services.workspaces import WorkspaceInvalid, bound_workspace
from .session import (
    BranchScan,
    JsonlSessionMetadata,
    JsonlSessionRepo,
    SessionError,
    SessionRecorder,
    branch_compaction,
    branch_tip,
    messages_for_branch,
)
from .web.app import (
    LOOPBACK_HOSTS,
    trusted_hosts,
)

# Derived from the tool registry so the help text cannot drift from the shipped toolset.
AGENT_TOOL_HELP = "（" + " / ".join(item["function"]["name"] for item in TOOLS) + "）"


def _local_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _session_indexer() -> SessionIndexer:
    """会话运行的索引器：只索引本进程写的会话（全库补齐留给 `avid web` 与 `avid index check`）。"""
    registry = WorkspaceRegistry()
    return SessionIndexer(
        roots=lambda: [userdirs.sessions_dir()],
        lookup_workspace=workspace_lookup(
            lambda: ((ws.id, ws.root, ws.name) for ws in registry.list(include_hidden=True))
        ),
    )


def _resolve_workspace(selection: str | None) -> Workspace:
    """Picks this command's workspace by id, path or the current directory, never writing the registry."""
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


def _start_mcp(state: RunState) -> None:
    """Starts this run's MCP servers, warning about failures instead of aborting the run."""
    manager = McpManager(state.workspace_root)
    state.mcp = manager
    for warning in manager.start_all():
        print(f"警告：{warning}", file=sys.stderr)


def _announce_security(security: RunSecurity | None) -> None:
    """Prints the run's security posture to stderr so a disabled sandbox is never invisible."""
    if security is None:  # pragma: no cover - callers guarantee a non-None security object
        return
    label = (
        f"{PERMISSION_FULL}（完全访问：跳过毁灭级确认、无沙箱）"
        if security.full
        else f"{PERMISSION_NORMAL}（默认：仅毁灭级命令双确认）"
    )
    print(f"[安全] {label}｜{security.sandbox.one_line()}", file=sys.stderr)
    for note in security.summary()["notes"]:
        print(f"[安全] {note}", file=sys.stderr)
    if security.sandbox.degraded:
        print(
            "⚠ 沙箱不可用：所有动作按默认形态直接执行（毁灭级仍双确认）。"
            "装好 bubblewrap（bwrap）可获得内核级隔离。",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avid",
        description="不带参数直接进入交互会话（/compact 压缩、/rewind 回滚上一轮、/<技能名> 载入技能）；"
        "带问题则单轮提问后退出",
    )
    parser.add_argument("prompt", nargs="?", help="要发送给模型的问题（缺省进入交互会话）")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="走 agent 循环，模型可调用已注册的工具" + AGENT_TOOL_HELP,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="替毁灭级确认答「是」（凭据拒读仍然生效，沙箱也仍然生效），"
        "非交互场景需显式指定",
    )
    parser.add_argument(
        "--allow-full-access",
        action="store_true",
        dest="allow_full_access",
        help="完全访问的**显式授权**开关：跳过毁灭级确认、关沙箱、不滤环境",
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
    """Dispatches to the matching mode and returns the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # Subcommands are split off before ordinary parsing, leaving the plain question form unchanged.
    if argv and argv[0] == "web":
        return _run_web(argv[1:])
    if argv and argv[0] == "workspace":
        return _run_workspace(argv[1:])
    if argv and argv[0] == "session":
        return _run_session_command(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_sessions or args.delete_session is not None:
        return _session_admin(args)
    if args.session and args.new_session:
        parser.error("--session 与 --new-session 只能选一个")
    if args.session_name and not (args.session or args.new_session):
        parser.error("--session-name 需要与 --session 或 --new-session 一起用")

    try:
        config = resolve_chat()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if not args.prompt:
        # 无问题 = 交互会话：--session/--new-session/--workspace 继续生效。
        return _interactive(args, config)

    if args.session or args.new_session:
        args.agent = True

    if args.agent:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
        # Silence the HTTP client so only Avid's own trace lines reach stderr.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        try:
            target = _resolve_workspace(args.workspace)
            state = RunState.for_run(
                auto_approve=args.yes,
                full=args.allow_full_access,
                workspace_root=target.root,
            )
        except (WorkspaceNotFound, WorkspaceInvalid) as exc:
            print(f"工作区错误：{exc}", file=sys.stderr)
            return 2
        _announce_security(state.security)
        _start_mcp(state)
        schemas, impls = build_toolset(state)
        if args.session or args.new_session:
            return _run_session(args, config, state)
        try:
            print(
                Run(
                    [{"role": "user", "content": args.prompt}],
                    RunSpec.resolve(config=config, tools=schemas, registry=impls),
                    state=state,
                ).run().text
            )
        except LLMError as exc:
            print(f"循环中止：{exc}", file=sys.stderr)
            return 1
        finally:
            # MCP server processes live exactly as long as the run does.
            state.close_mcp()
        return 0

    try:
        reply = ask(config, args.prompt)
    except LLMError as exc:
        print(f"调用失败：{exc}", file=sys.stderr)
        return 1

    print(reply.text)
    print(
        f"--- model={reply.model or config.model}"
        f"{usage_suffix(reply.usage, config)} ---",
        file=sys.stderr,
    )
    return 0


def usage_suffix(usage: Usage, config: Config) -> str:
    """Formats the one-line usage summary, omitting every figure the provider did not report."""
    parts = [
        f"prompt={usage.prompt_tokens}",
        f"completion={usage.completion_tokens}",
        f"total={usage.total_tokens}",
    ]
    if usage.cache_read_tokens is not None:
        parts.append(f"cache_read={usage.cache_read_tokens}")
        ratio = hit_ratio(usage)
        if ratio is not None:
            parts.append(f"hit={ratio:.0%}")
    if usage.cache_write_tokens is not None:
        parts.append(f"cache_write={usage.cache_write_tokens}")
    window = config.context_window
    if window:
        parts.append(f"window={window}")
        parts.append(f"util={usage.prompt_tokens / window:.0%}")
    return " " + " ".join(parts)


def _run_session(args: argparse.Namespace, config, state: RunState | None = None) -> int:
    """Resumes or creates a session and runs one loop, reading history and writing this round back."""
    try:
        target = _resolve_workspace(args.workspace)
    except WorkspaceNotFound as exc:
        print(f"工作区错误：{exc}", file=sys.stderr)
        return 2
    print(
        f"工作区 {target.id}（{target.root}）",
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
        indexer = _session_indexer()
        history = messages_for_branch(session, recorder.branch)
        recorder.ensure_branch()
        messages = [*history, {"role": "user", "content": args.prompt}]
        try:
            reply = Run(
                messages,
                RunSpec.resolve(config=config),
                state=state
                or RunState.for_run(
                    auto_approve=args.yes,
                    full=args.allow_full_access,
                    workspace_root=target.root,
                ),
                on_message=notifying(indexer, recorder.on_message, session.metadata.id),
                on_compaction=recorder.record_compaction,
            ).run().text
        except LLMError as exc:
            print(f"循环中止：{exc}", file=sys.stderr)
            return 1
        stats = session.get_stats()
    except SessionError as exc:
        print(f"会话错误：{exc}", file=sys.stderr)
        return 1
    finally:
        # 索引队列排空再走：终端这一轮的消息不该等到下次才被搜到。
        indexer.stop()
        if session is not None and not session.closed:
            session.close()
        repo.close()
        if state is not None:
            # MCP server processes live exactly as long as the run does.
            state.close_mcp()

    print(reply)
    print(
        f"--- session={session.metadata.id}"
        f" {'新建' if created else '续接'}"
        f" messages={stats.message_count} ---",
        file=sys.stderr,
    )
    return 0


def _interactive(args: argparse.Namespace, config) -> int:
    """交互会话：默认续接最近会话；/compact 压缩、/rewind 回滚上一轮、/<技能名> 载入技能、其余发给模型。"""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        target = _resolve_workspace(args.workspace)
    except WorkspaceNotFound as exc:
        print(f"工作区错误：{exc}", file=sys.stderr)
        return 2

    repo = JsonlSessionRepo(sessions_root(target), workspace=target.id)
    session = None
    created = False
    try:
        if args.new_session:
            session = repo.create(workspace=target.id)
            created = True
        elif args.session:
            existing = _find(repo, args.session)
            if existing is None:
                session = repo.create(id=args.session, workspace=target.id)
                created = True
            else:
                session = repo.open(existing)
        else:
            # 默认续接最近会话（repo.list() 按创建时间倒序）。
            items = repo.list()
            if items:
                session = repo.open(items[0])
            else:
                session = repo.create(workspace=target.id)
                created = True
        if args.session_name:
            session.set_name(args.session_name)
        recorder = SessionRecorder(session)
        indexer = _session_indexer()
        recorder.ensure_branch()
        # 写前快照随会话接线：files 工具覆盖前把原内容落进本会话的检查点目录，
        # 落点跟随分支 tip 条目。sink 全程复用（seq 单调递增，目录不冲突）。
        checkpoint = DirCheckpointSink(
            root=Path(target.root), session_id=session.metadata.id, tip_seq=recorder.tip_seq
        )
    except SessionError as exc:
        print(f"会话错误：{exc}", file=sys.stderr)
        if session is not None and not session.closed:
            session.close()
        repo.close()
        return 1

    print(
        f"工作区 {target.id}（{target.root}）\n"
        f"会话 {session.metadata.id}{'（新建）' if created else '（续接）'}；"
        "输入问题回车发送，/compact 压缩，/rewind 回滚上一轮，/<技能名> 载入技能，Ctrl-D 退出",
        file=sys.stderr,
    )
    try:
        while True:
            try:
                line = input("avid> ")
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                break
            text = line.strip()
            if not text:
                continue

            match = None
            if text.startswith("/"):
                match = commands_module.match_command(
                    text,
                    skill_names=commands_module.skill_names(workspace_root=target.root),
                )
            if match is not None and match.kind == commands_module.KIND_COMMAND:
                if match.name == "rewind":
                    _rewind_now(session, recorder, Path(target.root))
                else:
                    _compact_now(session, recorder, config, target)
                continue
            if match is not None and match.kind == commands_module.KIND_SKILL:
                body = commands_module.skill_text(match.name, workspace_root=target.root)
                if body is None:
                    print(
                        commands_module.help_text(workspace_root=target.root),
                        file=sys.stderr,
                    )
                    continue
                # 技能全文作为一条 user 消息写入会话：落库、可续接，下一轮模型即见。
                recorder.on_message({"role": "user", "content": body})
                print(f"已载入技能 {match.name}（{len(body)} 字符），已写入会话", file=sys.stderr)
                continue
            if match is not None and match.kind == commands_module.KIND_UNKNOWN:
                print(commands_module.help_text(workspace_root=target.root), file=sys.stderr)
                continue

            # 普通输入：每轮一份新的 RunState（安全默认沿用旗标与工作区），MCP 随运行起停。
            state = RunState.for_run(
                auto_approve=args.yes,
                full=args.allow_full_access,
                workspace_root=target.root,
            )
            state.checkpoint = checkpoint
            _start_mcp(state)
            try:
                schemas, impls = build_toolset(state)
                messages = [
                    *messages_for_branch(session, recorder.branch),
                    {"role": "user", "content": text},
                ]
                outcome = Run(
                    messages,
                    RunSpec.resolve(config=config, tools=schemas, registry=impls),
                    state=state,
                    on_message=notifying(indexer, recorder.on_message, session.metadata.id),
                    on_compaction=recorder.record_compaction,
                ).run()
            except LLMError as exc:
                print(f"循环中止：{exc}", file=sys.stderr)
                continue
            finally:
                state.close_mcp()
            print(outcome.text)
            print(f"--- {outcome.reason} ---", file=sys.stderr)
    finally:
        indexer.stop()
        if session is not None and not session.closed:
            session.close()
        repo.close()
    return 0


def _compact_now(session, recorder: SessionRecorder, config, target) -> None:
    """/compact 的执行体：强制压缩当前会话历史并落游标（摘要调用走非流式 chat）。"""
    report = commands_module.compact_session(
        history=messages_for_branch(session, recorder.branch),
        config=config,
        summarize=chat_completion,
        workspace_root=target.root,
        on_compaction=recorder.record_compaction,
    )
    if report is None:
        print("没有可压缩的更早历史（或摘要失败），会话保持不变", file=sys.stderr)
    else:
        print(f"已压缩：{report.describe()}", file=sys.stderr)


def _rewind_now(session, recorder: SessionRecorder, root: Path) -> None:
    """/rewind 的执行体：对话指针回移到最近一次用户输入之前，文件恢复到该点。

    条目只追加：被移出的对话留在盘上；压缩游标覆盖的前缀属于旧链，必须一并清掉，
    否则投影把摘要接在被回滚的链上。
    """
    branch = session.branch(recorder.branch)
    chain = (
        branch.find_entries(BranchScan(order="oldestFirst"))
        if branch is not None
        else []
    )
    found = rewind_target(chain)
    if found is None:
        print("没有可回滚的用户输入", file=sys.stderr)
        return
    if found.parent_id is None:
        session.delete_value(branch_tip(recorder.branch))
    else:
        session.set_value(branch_tip(recorder.branch), found.parent_id)
    session.delete_value(branch_compaction(recorder.branch))
    lines = restore(
        root=root, session_id=session.metadata.id, through_seq=found.through_seq
    )
    restored, deleted = restore_tally(lines)
    removed = sum(1 for entry in chain if entry.seq >= found.through_seq)
    print(
        f"已回滚：移出 {removed} 条；文件恢复 {restored} 个、删除 {deleted} 个",
        file=sys.stderr,
    )


def _session_admin(args: argparse.Namespace) -> int:
    """Lists or deletes sessions; neither calls a model, so no model config is needed first."""
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

    actions.add_parser("list", help="按最近使用列出已登记的工作区")

    remove = actions.add_parser(
        "remove", help="从候选列表里摘掉（立墓碑：会话数据与条目都留着）"
    )
    remove.add_argument("workspace", help="工作区 id 或路径")

    return parser


def _run_workspace(argv: list[str]) -> int:
    """Implements ``avid workspace``, the only writer of the registry; session data stays untouched."""
    args = build_workspace_parser().parse_args(argv)
    registry = WorkspaceRegistry()
    try:
        if args.action == "add":
            before = registry.find(args.path)
            ws = registry.add(args.path, name=args.name)
            if before is not None:
                print(f"已登记过，未重复添加：{ws.id}\t{ws.root}\t{ws.name}")
            else:
                print(f"{ws.id}\t{ws.root}\t{ws.name}")
            return 0

        if args.action == "list":
            items = registry.list()
            if not items:
                print("（还没有登记任何工作区；用 `avid workspace add <路径>` 登记）")
                return 0
            for ws in items:
                print(
                    f"{ws.id}\t{ws.root}\t{ws.name}"
                    f"\t{_local_time(ws.last_used_at)}"
                )
            return 0

        ws = registry.remove(args.workspace)
        print(
            f"已从候选列表里摘掉 {ws.id}（{ws.root}）；"
            "会话与磁盘数据都留着（它的会话在界面上归「未归属的会话」），"
            "`avid workspace add` 同一个路径即可撤销"
        )
        return 0
    except WorkspaceError as exc:
        print(f"工作区错误：{exc}", file=sys.stderr)
        return 1


def build_session_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avid session",
        description="会话目录：看它在哪，以及把旧布局里的会话搬进来",
    )
    actions = parser.add_subparsers(dest="action", required=True)

    actions.add_parser("dir", help="打印当前会话目录与它的来源")

    migrate = actions.add_parser(
        "migrate",
        help="把旧布局（<工作区根>/.avid/sessions）里的会话搬进会话目录",
    )
    migrate.add_argument(
        "--from",
        dest="from_dir",
        metavar="DIR",
        help="额外扫一个目录：集中目录（下面按工作区 id 分子目录）或平铺目录",
    )
    migrate.add_argument("--yes", action="store_true", help="不再问一次，直接搬")

    search = actions.add_parser("search", help="按内容检索会话（本地索引，不调模型）")
    search.add_argument("query", help="要搜的词；多个词按 AND，两字中文词走扫描")
    search.add_argument("--workspace", metavar="PATH|ID", help="只搜这个工作区")
    search.add_argument("--session", metavar="ID", help="只搜这个会话")
    search.add_argument("--limit", type=int, default=20, help="最多给几条（默认 20）")
    search.add_argument("--open", action="store_true", help="直接续接第一条命中的会话")

    return parser


def _run_session_command(argv: list[str]) -> int:
    """Implements ``avid session``: 会话目录的只读查询与一次性搬迁，都不唤起模型。"""
    args = build_session_parser().parse_args(argv)
    if args.action == "dir":
        return _session_dir_report()
    if args.action == "search":
        return _session_search(args)
    return _session_migrate(args)


def _known_roots() -> list[str]:
    """会去扫旧会话目录的工作区根：注册表里的（含墓碑）+ 当前目录。"""
    roots = [ws.root for ws in WorkspaceRegistry().list(include_hidden=True)]
    current = str(Path(workspace.WORKSPACE_ROOT).resolve())
    if current not in roots:
        roots.append(current)
    return roots


def _session_dir_report() -> int:
    store = userdirs.sessions_dir()
    source = userdirs.sessions_dir_source()
    decided = {
        "env": f"环境变量 {userdirs.SESSIONS_DIR_ENV}",
        "settings": str(userdirs.settings_path()),
        "default": "默认位置",
    }[source]
    print(f"{store}\t来源：{decided}")
    if source != "default":
        print(f"默认位置：{userdirs.default_sessions_dir()}", file=sys.stderr)
    # 旧位置还有会话时提一句：否则用户会以为会话丢了。
    plan = plan_migration(roots=_known_roots(), store=store)
    if plan.moves:
        print(
            f"另有 {len(plan.moves)} 个会话还在旧位置，"
            "用 `avid session migrate` 搬进来（先看清单再决定）",
            file=sys.stderr,
        )
    return 0


def _session_migrate(args: argparse.Namespace) -> int:
    plan = plan_migration(roots=_known_roots(), from_dir=args.from_dir)
    if not plan.moves:
        print("没有可搬的会话。")
    else:
        print(f"会话目录：{plan.store}")
        print(f"要搬 {len(plan.moves)} 个会话：")
        for move in plan.moves:
            print(f"  {move.source} → {move.target}")
        if not args.yes and not _confirm("现在搬？[y/N] "):
            print("没搬（清单可以重看一遍再决定）。", file=sys.stderr)
            return 1
        tally = apply_migration(plan)
        print(
            f"已搬 {len(tally.moved)} 个，跳过 {len(tally.skipped)} 个，"
            f"清掉 {len(tally.removed_dirs)} 个空的旧目录"
        )
        for skip in tally.skipped:
            print(f"  跳过 {skip.source}：{skip.reason}", file=sys.stderr)
        return 0
    for skip in plan.skips:
        print(f"  跳过 {skip.source}：{skip.reason}", file=sys.stderr)
    return 0


def _session_search(args: argparse.Namespace) -> int:
    """按内容检索：先把索引补到最新（一遍 reconcile），再查，再按命中给人话。"""
    indexer = _session_indexer()
    try:
        indexer.reconcile()
        hits = queries.search_entries(
            indexer.conn,
            args.query,
            session_id=args.session,
            workspace_id=args.workspace,
            limit=max(1, args.limit),
        )
        behind = queries.index_stats(indexer.conn)["behind"]
    finally:
        indexer.close()

    if not hits:
        print("没有命中。")
        if behind:
            print(f"（索引还落后 {behind} 个会话，稍后再跑一次可能就有了）", file=sys.stderr)
        return 0

    for hit in hits:
        where = hit.workspace_name or hit.workspace_id or "未归属"
        print(
            f"{_local_time(hit.timestamp or 0)}  {where}  {hit.title or '未命名会话'}"
            f"  [{hit.role or hit.entry_type}]"
        )
        print(f"    {hit.snippet}")
        print(f"    会话 {hit.session_id}  条目 {hit.entry_id}  seq {hit.seq}")
    print(f"--- 命中 {len(hits)} 条 ---", file=sys.stderr)
    if behind:
        print(f"（索引还落后 {behind} 个会话）", file=sys.stderr)

    if args.open:
        try:
            config = resolve_chat()
        except ConfigError as exc:
            print(f"配置错误：{exc}", file=sys.stderr)
            return 2
        return _interactive(
            argparse.Namespace(
                workspace=hits[0].workspace_id,
                session=hits[0].session_id,
                new_session=False,
                session_name=None,
                yes=False,
                allow_full_access=False,
            ),
            config,
        )
    return 0


def _confirm(prompt: str) -> bool:
    """Terminal yes/no; a closed stdin answers no, so a pipe can never approve a destructive step."""
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


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
    """Starts uvicorn, turning a missing optional web dependency into an actionable message."""
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
        f"Avid Web 正在监听 http://{args.host}:{args.port}",
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
    """Builds the Host and Origin allow-list for this bind address, warning when it is not loopback."""
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
    except OSError:  # pragma: no cover - trust only the explicitly given hosts if lookup fails
        pass
    return trusted_hosts(frozenset(item for item in extra if item))


def _host_of(value: str) -> str:
    text = (value or "").strip().lower()
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    return text.rsplit(":", 1)[0]


def _peek(repo: JsonlSessionRepo, meta: JsonlSessionMetadata) -> tuple[str | None, int]:
    """Reads a session's name and count through the non-replaying summary path, degrading quietly."""
    try:
        summary = repo.summarize(meta)
    except SessionError:
        return None, 0
    return summary.name, summary.message_count
