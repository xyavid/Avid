"""The kernel's runtime dependency set, as an executable property rather than a comment.

Module-level imports across `avid/` (minus the `avid/web/` transport adapter) are the standard
library plus httpx and `avid` itself, and pyproject declares exactly that. The single deferred
exception is `uvicorn` inside `avid/cli.py::_run_web`, which the existing A2 gate already pins to
the web subcommand. This gate is why adding an HTML extraction library or an HTTP SDK fails here
instead of quietly widening the install.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ALLOWED_THIRD_PARTY = {"httpx"}
#: file -> the third-party roots allowed there, and only as a deferred (non-module-level) import.
DEFERRED_ALLOWANCES = {"avid/cli.py": {"uvicorn"}}
ROOT = Path(__file__).resolve().parent.parent
AVID = ROOT / "avid"


def kernel_files() -> list[Path]:
    """Every kernel module, with the transport adapter excluded on purpose."""
    return [
        path
        for path in sorted(AVID.rglob("*.py"))
        if "__pycache__" not in path.parts and path.relative_to(AVID).parts[0] != "web"
    ]


def _roots(nodes: list[ast.stmt]) -> set[str]:
    roots: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _is_type_checking(test: ast.expr) -> bool:
    name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", "")
    return name == "TYPE_CHECKING"


def module_level_roots(path: Path) -> set[str]:
    """Imports executed just by importing the module; `if TYPE_CHECKING` blocks do not run."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reachable = [node for node in tree.body if not (isinstance(node, ast.If) and _is_type_checking(node.test))]
    return _roots(reachable)


def all_level_roots(path: Path) -> set[str]:
    """Every import in the file, including ones deferred inside functions."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        roots |= _roots([node])
    return roots


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def test_modules_import_nothing_beyond_the_standard_library_and_httpx():
    offenders: dict[str, list[str]] = {}
    for path in kernel_files():
        for root in module_level_roots(path):
            if root == "avid" or root in sys.stdlib_module_names or root in ALLOWED_THIRD_PARTY:
                continue
            offenders.setdefault(root, []).append(_relative(path))

    assert not offenders, (
        f"内核在模块层出现了标准库与 httpx 之外的依赖：{offenders}。"
        "要去掉，或在 pyproject、CONTRIBUTING.md 与本门禁里显式承认这次扩权。"
    )


def test_the_only_deferred_third_party_import_is_uvicorn_in_the_cli():
    found: dict[str, list[str]] = {}
    for path in kernel_files():
        allowed = DEFERRED_ALLOWANCES.get(_relative(path), set())
        extra = all_level_roots(path) - module_level_roots(path) - sys.stdlib_module_names - {"avid"}
        for root in extra - ALLOWED_THIRD_PARTY - allowed:
            found.setdefault(root, []).append(_relative(path))

    assert not found, f"出现了未声明的延后导入：{found}；Web 依赖只有 cli.py 的 uvicorn 一处"


def test_declared_runtime_dependencies_are_exactly_httpx():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {re.split(r"[<>=!~\[;\s]", dep, maxsplit=1)[0] for dep in data["project"]["dependencies"]}

    assert names == ALLOWED_THIRD_PARTY
