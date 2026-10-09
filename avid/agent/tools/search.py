"""grep_search：在工作区里按内容找东西（正则、带行号、默认忽略大小写）。

它取代「让模型用 bash 拼一条 grep」这条路，有三个好处：结果是结构化的（路径:行号:文本，
直接能念给模型听）、**并发档是 safe**（bash 是独占屏障，一次搜索会把同批的读全堵住）、
以及输出有上限（命中上千条时给的是「前 N 条 + 还有多少」，不是一屏噪声）。

实现在 rg 与纯 Python 之间二选一：本机有 ripgrep 就走 `rg --json`（尊重 .gitignore、
跳过二进制、快一个量级），没有就回落 Python 遍历（尊重同一份忽略名单）。两条路都给
同一种输出，所以模型看到的形状与机器上装了什么无关。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .registry import tool
from .workspace import relative, resolve

if TYPE_CHECKING:  # annotation only: tools must not depend on runtime at run time
    from ..state import RunState

logger = logging.getLogger("avid.agent.tools.search")

# 一次搜索最多回多少条命中（再多对模型没用，只会挤掉别的上下文）。
MAX_RESULTS = 60
HARD_RESULT_CAP = 200
# 单行最长多少字符（长行——压缩过的 JS、日志——会把上下文撑爆）。
MAX_LINE_CHARS = 300
# 两条路共用的墙钟预算：超了就报「超时 + 已找到的部分」，而不是把运行卡住。
TIME_BUDGET_SECONDS = 20.0
# 回落遍历时跳过的目录（rg 靠 .gitignore；Python 这条路只能自己列名单）。
SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)
# 回落遍历时单个文件的读取上限：再大就不像源码了，跳过并如实说。
MAX_FILE_BYTES = 2 * 1024 * 1024


@tool(
    name="grep_search",
    description="在工作区里按内容搜索（正则），返回 路径:行号:文本。"
    "默认忽略大小写、尊重 .gitignore、跳过二进制与常见依赖目录；"
    "用 glob 参数限定文件类型（如 *.py、src/**/*.ts）。只搜索，不修改任何文件。",
    properties={
        "pattern": {
            "type": "string",
            "description": "正则表达式，例如 def\\s+handler、TODO|FIXME、会话存储。",
        },
        "path": {
            "type": "string",
            "description": '起始目录或文件，相对工作区根目录，默认 "."。',
        },
        "glob": {
            "type": "string",
            "description": "只搜匹配这个 glob 的文件，例如 *.py。",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "是否区分大小写，默认 false（忽略大小写）。",
        },
        "max_results": {
            "type": "integer",
            "description": f"最多返回多少条命中，默认 {MAX_RESULTS}，上限 {HARD_RESULT_CAP}。",
        },
    },
    required=("pattern",),
    concurrency="safe",
)
def grep_search(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Searches file contents inside the workspace and returns path:line:text hits."""
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return "错误：缺少参数 pattern"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"错误：正则非法：{exc}"

    raw_path = str(args.get("path", ".") or ".")
    root, error = resolve(raw_path, root=_root(state), outside_ok=True)
    if error:
        return f"错误：{error}"
    assert root is not None  # resolve yields exactly one of a path or an error
    if not root.exists():
        return f"错误：路径不存在：{raw_path}"

    glob = str(args.get("glob", "") or "").strip() or None
    case_sensitive = bool(args.get("case_sensitive", False))
    limit = _limit(args.get("max_results"))
    budget = time.monotonic() + TIME_BUDGET_SECONDS
    base = _root(state)

    if shutil.which("rg"):
        hits, truncated, note = _search_with_rg(
            pattern, root, glob=glob, case_sensitive=case_sensitive, limit=limit, budget=budget
        )
    else:
        hits, truncated, note = _search_with_python(
            pattern,
            root,
            glob=glob,
            case_sensitive=case_sensitive,
            limit=limit,
            budget=budget,
        )

    rendered = [f"{relative(path, root=base)}:{number}: {text}" for path, number, text in hits]
    if not rendered:
        return f"未找到匹配 {pattern} 的内容" + (f"（{note}）" if note else "")
    body = "\n".join(rendered)
    if truncated:
        body += f"\n…（命中更多，只显示前 {len(rendered)} 条；用 glob 限定范围或写更精确的 pattern）"
    if note:
        body += f"\n（{note}）"
    return body


def _limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return MAX_RESULTS
    return max(1, min(number, HARD_RESULT_CAP))


def _root(state: "RunState | None") -> Path | None:
    """Returns the run's workspace root, or None to let the workspace module supply a default."""
    raw = getattr(state, "workspace_root", None)
    return Path(raw) if raw else None


def _clean(text: str) -> str:
    """One result line: collapsed whitespace and capped, because raw lines can be enormous."""
    return " ".join(text.split())[:MAX_LINE_CHARS]


def _matches_glob(path: Path, pattern: str | None, *, root: Path) -> bool:
    if pattern is None:
        return True
    try:
        return path.match(pattern) or path.relative_to(root).match(pattern)
    except (ValueError, OSError):
        return False


def _search_with_rg(
    pattern: str,
    root: Path,
    *,
    glob: str | None,
    case_sensitive: bool,
    limit: int,
    budget: float,
) -> tuple[list[tuple[Path, int, str]], bool, str]:
    """rg --json: structured hits, ignore files honoured, binaries skipped by default."""
    argv = ["rg", "--json", "--line-number", "--no-heading", "--max-columns", str(MAX_LINE_CHARS)]
    if not case_sensitive:
        argv.append("--ignore-case")
    # 明确排除依赖目录：rg 只认 .gitignore，而工作区未必是 git 仓库（临时目录里就没有），
    # 那样 node_modules 会被搜——两条路的行为必须一致，所以排除名单显式传给 rg。
    for name in sorted(SKIP_DIRS):
        argv += ["--glob", f"!**/{name}/**"]
    if glob:
        argv += ["--glob", glob]
    if root.is_dir():
        argv += ["--max-filesize", "2M"]
    argv += ["--", pattern, str(root)]

    truncated = False
    hits: list[tuple[Path, int, str]] = []
    note = ""
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:  # pragma: no cover - rg 在 which 查过之后才起不来（权限/竞态）
        return [], False, f"rg 起不来（{exc}），请改用更小的 path 或用 bash 里的 grep"
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if time.monotonic() > budget:
                truncated = True
                note = f"搜索超时（>{int(TIME_BUDGET_SECONDS)} 秒），以下是已找到的部分"
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data") or {}
            path_text = (data.get("path") or {}).get("text")
            number = data.get("line_number")
            text = (data.get("lines") or {}).get("text")
            if not isinstance(path_text, str) or not isinstance(number, int):
                continue
            hits.append((Path(path_text), number, _clean(str(text or ""))))
            if len(hits) >= limit:
                truncated = True
                break
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:  # pragma: no cover - 收尾兜底
                process.kill()
        process.wait()
        if process.stderr is not None:
            process.stderr.close()
    return hits, truncated, note


def _search_with_python(
    pattern: str,
    root: Path,
    *,
    glob: str | None,
    case_sensitive: bool,
    limit: int,
    budget: float,
) -> tuple[list[tuple[Path, int, str]], bool, str]:
    """Fallback walk: same output shape, no ripgrep dependency, conservative ignore list."""
    flags = re.compile(pattern) if case_sensitive else re.compile(pattern, re.IGNORECASE)

    files = [root] if root.is_file() else _walk_files(root, budget=budget)
    hits: list[tuple[Path, int, str]] = []
    truncated = False
    note = ""
    for path in files:
        if time.monotonic() > budget:
            truncated = True
            note = f"搜索超时（>{int(TIME_BUDGET_SECONDS)} 秒），以下是已找到的部分"
            break
        if not _matches_glob(path, glob, root=root if root.is_dir() else root.parent):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for number, line in enumerate(handle, start=1):
                    if flags.search(line):
                        hits.append((path, number, _clean(line)))
                        if len(hits) >= limit:
                            truncated = True
                            break
        except OSError:
            continue
        if truncated:
            break
    return hits, truncated, note


def _walk_files(root: Path, *, budget: float) -> list[Path]:
    """Breadth-first walk with a skip list and a time budget, so a huge tree cannot hang the run."""
    found: list[Path] = []
    queue = [root]
    while queue:
        current = queue.pop()
        if time.monotonic() > budget:
            break
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        continue
                    queue.append(entry)
                elif entry.is_file():
                    found.append(entry)
            except OSError:  # pragma: no cover - 权限/竞态：跳过即可
                continue
    return sorted(found)


__all__ = ["HARD_RESULT_CAP", "MAX_RESULTS", "grep_search"]
