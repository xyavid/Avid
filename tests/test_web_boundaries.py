"""A1 - A14 architecture boundaries, held by grep and AST assertions without running anything.

The gates match literals only: concatenated URLs and indirect imports stay outside their reach.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "avid"
WEB = ROOT / "web"

# services/ is kernel-side too: closest to the transport layer, it is the likeliest place to
# pick up an accidental pydantic import.
KERNEL_PACKAGES = ("agent", "providers", "security", "session", "services")


def files_under(*parts: str, suffix: str = ".py") -> list[Path]:
    return sorted(SRC.joinpath(*parts).rglob(f"*{suffix}"))


def hits(paths: list[Path], pattern: str) -> list[str]:
    regex = re.compile(pattern)
    found: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if regex.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    return found


def frontend_sources() -> list[Path]:
    if not WEB.is_dir():
        return []
    return sorted(
        [*WEB.joinpath("src").rglob("*.ts"), *WEB.joinpath("src").rglob("*.tsx")]
    )


def code_hits(paths: list[Path], pattern: str) -> list[str]:
    """Match code lines only; a type name in a comment or docstring is not a call site."""
    return [
        item
        for item in hits(paths, pattern)
        if not item.split(": ", 1)[1].lstrip().startswith(("#", '"""', "'''"))
    ]


# ---------------- A1 / A2 ----------------


def test_a1_kernel_does_not_depend_on_a_web_framework():
    assert hits(files_under(*KERNEL_PACKAGES), r"fastapi|pydantic|starlette|uvicorn") == []


def test_a2_only_web_knows_http_frameworks():
    # Every "fastapi" hit under avid/ must be inside web/ (cli.py imports uvicorn for the web
    # subcommand: the one allowed spot).
    found = hits(files_under(suffix=".py"), r"\bfastapi\b")
    assert found, "应该至少有一处 fastapi"
    offenders = [item for item in found if "/web/" not in item.split(":")[0]]
    assert offenders == [], f"内核里出现了 HTTP 框架：{offenders}"


def test_a2_uvicorn_is_only_used_for_the_web_subcommand():
    found = hits(files_under(suffix=".py"), r"^\s*(import|from)\s+uvicorn")
    assert {item.split(":")[0] for item in found} == {"avid/cli.py"}


def test_a1_services_is_also_checked_for_web_framework_imports():
    """The A1 package list must include services: it sits nearest the transport layer."""
    assert "services" in KERNEL_PACKAGES
    assert hits(files_under("services"), r"fastapi|pydantic|starlette|uvicorn") == []


# ---------------- A3 ----------------


def test_a3_run_is_the_only_loop_and_stays_a_scheduler():
    # Run in agent/run.py is the only loop: scheduling has exactly one hook trigger point
    # (UserPromptSubmit; Stop lives in stop.py) and never a hand-written while.
    run = (SRC / "agent" / "run.py").read_text(encoding="utf-8")
    stop = (SRC / "agent" / "stop.py").read_text(encoding="utf-8")
    assert run.count("state.hooks.trigger(") == 1, (
        "调度层只该有 UserPromptSubmit 一个 hook 调用点（Stop 在 stop.decide）"
    )
    assert stop.count("state.hooks.trigger(") == 1, "终止路径只该有 Stop 一个 hook 调用点"
    assert "while " not in run, "循环里不该出现手写 while"

    # There is no second loop: Run has exactly three callers (run.py defines without calling).
    callers = {
        item.split(":")[0]
        for item in code_hits(files_under(suffix=".py"), r"\bRun\(")
    }
    assert callers == {
        "avid/cli.py",
        "avid/services/runs.py",
        "avid/agent/tools/subagent.py",
    }, callers

    # services / web never drive scheduling per round (no while/for round).
    assert hits(files_under("services") + files_under("web"), r"for round|while .*round") == []


def test_a3_observation_points_are_declared_once():
    run = (SRC / "agent" / "run.py").read_text(encoding="utf-8")
    assert "on_message" in run and "on_event" in run


# ---------------- A4 / A5 ----------------


def test_a4_services_does_not_import_web():
    assert hits(files_under("services"), r"avid\.web|from \.\.web") == []


def test_a5_svc_only_maps_kernel_errors():
    found = hits(files_under("services"), r"\bLLMError\b")
    # Only "catch and map" sites are allowed: the except branch in runs.py.
    assert found, "services 应当显式把内核异常映射成 run_failed"
    for item in found:
        assert "services/runs.py" in item, item


# ---------------- A6 ----------------


def test_a6_event_names_are_single_sourced():
    """Event-name literals live only in events.py; elsewhere the events.XXX constants are used."""
    names = ("run_started", "run_finished", "tool_call_started", "tool_call_finished")
    pattern = "|".join(f'"{name}"' for name in names)
    found = [
        item
        for item in hits(files_under(suffix=".py"), pattern)
        if "avid/agent/events.py" not in item.split(":")[0]
    ]
    assert found == [], f"事件名字面量泄漏到 events.py 之外：{found}"


def test_a10_the_message_callback_is_wired_in_a_known_set_of_places():
    """The message callback appears only in these places; one more means declaring it here with a
    reason (run.py calls it, recorder.py exposes it, cli.py and services/runs.py wire it to runs,
    services/sessions.py copies history through it directly).
    """
    found = {
        item.split(":")[0]
        for item in hits(files_under(suffix=".py"), r"on_message")
    }
    assert found == {
        "avid/agent/run.py",
        "avid/session/recorder.py",
        "avid/cli.py",
        "avid/services/runs.py",
        "avid/services/sessions.py",
    }, found


# ---------------- A11 ----------------


def test_a11_recorder_remains_the_only_session_writer():
    """Session content reaches disk only through SessionRecorder."""
    for package in ("web", "services", "agent", "providers"):
        assert hits(files_under(package), r"append_message|\.commit\(") == [], package


# ---------------- A12 ----------------


def test_a12_frontend_has_no_third_party_urls_outside_api():
    # Exceptions: URL fixtures under __tests__/ (literals as input, no request), the www.w3.org/
    # XML namespace name SVG data URLs require (not a callable endpoint), and the http://${
    # prefix GFM autolinking adds (the host comes from model output). The rule targets runtime
    # third-party endpoints only.
    found = [
        item
        for item in hits(frontend_sources(), r"https?://")
        if not item.split(":")[0].startswith("web/src/api/")
        and "/__tests__/" not in item.split(":")[0]
        and "www.w3.org/" not in item
        and "http://${" not in item
    ]
    assert found == [], f"前端在 api/ 之外直连了第三方：{found}"


def test_frontend_sources_exist():
    """A12 asserts an empty set, which would pass vacuously without web/src; pin the premise."""
    assert frontend_sources(), "web/src 下没有前端源码"


# ---------------- A13: the agent -> security edge (policy stays out of scheduling) ----------------


def policy_imports(path: Path) -> tuple[set[str], set[str]]:
    """Return (security modules imported at runtime, security modules imported for typing only).

    AST, not grep: the gate separates annotation-only imports from real dependencies, and that
    distinction is the whole point of this edge.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    runtime: set[str] = set()
    typing_only: set[str] = set()

    def walk(body: list[ast.stmt], in_type_checking: bool) -> None:
        for node in body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                walk(node.body, True)
                walk(node.orelse, in_type_checking)
                continue
            if isinstance(node, ast.ImportFrom) and node.level:
                module = node.module or ""
                if module == "security" or module.startswith("security."):
                    # A package-level import is cross-layer use too: record security.permission.
                    targets = {
                        f"security.{alias.name}" for alias in node.names if module == "security"
                    } | ({module} if module != "security" else {"security"})
                    (typing_only if in_type_checking else runtime).update(targets)
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if isinstance(nested, list) and nested and not isinstance(node, ast.If):
                    walk(nested, in_type_checking)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.With, ast.Try)):
                walk(node.body, in_type_checking)
                walk(getattr(node, "handlers", []) or [], in_type_checking)

    walk(tree.body, False)
    return runtime, typing_only


# Declared agent -> security edges, written as an assertion instead of an exemption list:
# scheduling (run), context assembly (context/compaction), the tool protocol (execution), stop
# and spec carry zero runtime security dependency. Only three edges exist: state (holds the
# RunSecurity instance), hooks (registers the permission default callback) and files (the one
# hard deny: credential reads). RunSecurity / ApprovalLedger names appear only under
# TYPE_CHECKING.
AGENT_SECURITY_EDGES: dict[str, set[str]] = {
    "avid/agent/state.py": {"security.permission"},
    "avid/agent/hooks.py": {"security.permission"},
    # files takes only the credential gate (sensitive_reason)
    "avid/agent/tools/files.py": {"security.action"},
    # shell takes only the read-only fact (is_read_only) to decide parallel eligibility; the
    # verdict stays in security/engine and is deliberately out of the tool layer's reach.
    "avid/agent/tools/shell.py": {"security.command_parse"},
}
SECURITY_FREE_AGENT = (
    "avid/agent/transcript.py",
    "avid/agent/execution.py",
    "avid/agent/spec.py",
    "avid/agent/run.py",
    "avid/agent/stop.py",
    "avid/agent/context.py",
    "avid/agent/compaction.py",
    "avid/agent/todo.py",
    "avid/agent/prompt.py",
    "avid/agent/skills.py",
    "avid/agent/tools/subagent.py",
)


def test_a13_scheduling_and_tools_have_zero_runtime_security_dependency():
    for name in SECURITY_FREE_AGENT:
        runtime, _typing = policy_imports(ROOT / name)
        assert runtime == set(), f"{name} 出现了对安全层的运行时依赖：{sorted(runtime)}"


def test_a13_agent_security_edges_are_exactly_the_declared_ones():
    """Every runtime agent -> security import must be a declared edge (declare new ones here)."""
    for name in SECURITY_FREE_AGENT:
        assert name not in AGENT_SECURITY_EDGES

    seen: dict[str, set[str]] = {}
    for path in files_under("agent"):
        runtime, _typing = policy_imports(path)
        if runtime:
            seen[str(path.relative_to(ROOT))] = runtime

    assert seen == AGENT_SECURITY_EDGES, (
        "agent→security 的实际边与申报集不一致："
        f"{sorted(set(seen) | set(AGENT_SECURITY_EDGES))}"
    )


def test_a13_type_checking_imports_stay_inert():
    """Annotation imports stay inert: those names appear only under TYPE_CHECKING, so the zero
    runtime dependency means the import never executes, not just that the name is absent.
    """
    for name in (
        "avid/agent/spec.py",
        "avid/agent/run.py",
        "avid/agent/stop.py",
    ):
        _runtime, typing_only = policy_imports(ROOT / name)
        assert typing_only <= {"security.permission"}, f"{name}: {sorted(typing_only)}"


def test_web_imports_name_submodules_not_the_package():
    """`from . import <submodule>` is banned inside web/ because it closes a package cycle
    (__init__ -> app -> routes -> package), while `from . import current_services` stays legal:
    the AST check resolves imported names against real module files, and that is a function.
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "avid" / "web").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module or not node.level:
                continue
            package = path.parent
            for _ in range(node.level - 1):
                package = package.parent
            for alias in node.names:
                if (package / f"{alias.name}.py").is_file() or (
                    package / alias.name / "__init__.py"
                ).is_file():
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} "
                        f"from {'.' * node.level} import {alias.name}"
                    )
    assert offenders == [], f"web 内部按子模块名 import：{offenders}"


# ---------------- A14: index direction (a derived layer never pollutes truth) ----------------


def test_a14_the_session_package_does_not_know_about_the_index():
    """The index may read the session disk format; session/ must not import index/, because JSONL
    is the only authority and the index must stay fully rebuildable from it.
    """
    found = hits(files_under("session"), r"^\s*from\s+\.+.*\bindex\b|^\s*import\s+\S*\bindex\b")
    assert found == [], found


def test_a14_the_index_only_depends_on_the_layers_under_it():
    """index/ depends only on session/ and security/, never on services / web / agent / providers:
    notifications are sent by the wiring layer, so the index never needs to know who writes.
    """
    found = hits(files_under("index"), r"^\s*from\s+\.\.(agent|services|providers|web)\b")
    assert found == [], found
