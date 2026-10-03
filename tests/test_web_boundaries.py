"""A1 – A6 / A10 – A13：架构边界用 grep 与断言守住（不依赖运行）。

这些规则的价值在于它们**会失败**：一次「顺手 import 一下」会被立刻拦住。
边界是正则的边界——它只匹配字面量，拼接出来的 URL 与间接 import 不在覆盖内。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "avid"
WEB = ROOT / "web"

# svc/ 也在内核侧：它是最容易被"顺手 import 一下 pydantic"的层（离传输层最近），
# 而 A1 以前只查这五个包——`pyproject.toml` 那句"web/ 是唯一 importer"因此少了
# 一半的守护（审查里的 P2-22）。
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
    """只匹配代码行：跳过整行注释与 docstring 起止行（引用类型名不算调用点）。"""
    return [
        item
        for item in hits(paths, pattern)
        if not item.split(": ", 1)[1].lstrip().startswith(("#", '"""', "'''"))
    ]


# ---------------- A1 / A2 ----------------


def test_a1_kernel_does_not_depend_on_a_web_framework():
    assert hits(files_under(*KERNEL_PACKAGES), r"fastapi|pydantic|starlette|uvicorn") == []


def test_a2_only_web_knows_http_frameworks():
    # 判定原文：`grep -rln "fastapi" avid` 必须全部落在 web/。
    # （`avid web` 子命令在 cli.py 里 import uvicorn，是唯一放行点。）
    found = hits(files_under(suffix=".py"), r"\bfastapi\b")
    assert found, "应该至少有一处 fastapi"
    offenders = [item for item in found if "/web/" not in item.split(":")[0]]
    assert offenders == [], f"内核里出现了 HTTP 框架：{offenders}"


def test_a2_uvicorn_is_only_used_for_the_web_subcommand():
    found = hits(files_under(suffix=".py"), r"^\s*(import|from)\s+uvicorn")
    assert {item.split(":")[0] for item in found} == {"avid/cli.py"}


def test_a1_services_is_also_checked_for_web_framework_imports():
    """A1 的包清单必须含 services：它离传输层最近，最容易顺手 import pydantic。"""
    assert "services" in KERNEL_PACKAGES
    assert hits(files_under("services"), r"fastapi|pydantic|starlette|uvicorn") == []


# ---------------- A3 ----------------


def test_a3_run_is_the_only_loop_and_stays_a_scheduler():
    # 唯一的循环是 agent/run.py 的 Run：调度段只该有 UserPromptSubmit 一个
    # hook 触发点（Stop 的触发点在终止路径 stop.py），也不该有手写 while。
    run = (SRC / "agent" / "run.py").read_text(encoding="utf-8")
    stop = (SRC / "agent" / "stop.py").read_text(encoding="utf-8")
    assert run.count("state.hooks.trigger(") == 1, (
        "调度层只该有 UserPromptSubmit 一个 hook 调用点（Stop 在 stop.decide）"
    )
    assert stop.count("state.hooks.trigger(") == 1, "终止路径只该有 Stop 一个 hook 调用点"
    assert "while " not in run, "循环里不该出现手写 while"

    # 没有第二份循环：Run 的调用点固定为「三个接线点」（run.py 只定义不调用）。
    callers = {
        item.split(":")[0]
        for item in code_hits(files_under(suffix=".py"), r"\bRun\(")
    }
    assert callers == {
        "avid/cli.py",
        "avid/services/runs.py",
        "avid/agent/tools/subagent.py",
    }, callers

    # services / web 不按轮次自己推进调度（while/for round）
    assert hits(files_under("services") + files_under("web"), r"for round|while .*round") == []


def test_a3_observation_points_are_declared_once():
    run = (SRC / "agent" / "run.py").read_text(encoding="utf-8")
    assert "on_message" in run and "on_event" in run


# ---------------- A4 / A5 ----------------


def test_a4_services_does_not_import_web():
    assert hits(files_under("services"), r"avid\.web|from \.\.web") == []


def test_a5_svc_only_maps_kernel_errors():
    found = hits(files_under("services"), r"\bLLMError\b")
    # 只允许出现"捕获并映射"的地方：runs.py 的 except 分支
    assert found, "services 应当显式把内核异常映射成 run_failed"
    for item in found:
        assert "services/runs.py" in item, item


# ---------------- A6 ----------------


def test_a6_event_names_are_single_sourced():
    """事件名字面量只允许出现在 events.py（其余地方必须用 events.XXX 常量）。"""
    names = ("run_started", "run_finished", "tool_call_started", "tool_call_finished")
    pattern = "|".join(f'"{name}"' for name in names)
    found = [
        item
        for item in hits(files_under(suffix=".py"), pattern)
        if "avid/agent/events.py" not in item.split(":")[0]
    ]
    assert found == [], f"事件名字面量泄漏到 events.py 之外：{found}"


def test_a10_on_message_wiring_stays_in_four_places():
    found = {
        item.split(":")[0]
        for item in hits(files_under(suffix=".py"), r"on_message")
    }
    assert found == {
        "avid/agent/run.py",
        "avid/session/recorder.py",
        "avid/cli.py",
        "avid/services/runs.py",
    }, found


# ---------------- A11 ----------------


def test_a11_recorder_remains_the_only_session_writer():
    assert hits(files_under("web"), r"append_message|\.commit\(") == []
    assert hits(files_under("svc"), r"append_message|\.commit\(") == []


# ---------------- A12 ----------------


def test_a12_frontend_has_no_third_party_urls_outside_api():
    # 两类例外，都不是「直连第三方」：
    #   · `__tests__/`：URL 夹具（例如 sanitizeUrl 的用例）必须拿真实字面量当输入，
    #     而它们不产生请求；
    #   · `www.w3.org/`：XML 命名空间标识（`xmlns="http://www.w3.org/2000/svg"`）。
    #     它是格式要求的名字，不是可请求的端点——SVG 数据地址里必须有它，
    #     浏览器才认这是 SVG。规则拦的是运行时代码里的第三方端点，这两类都不沾边。
    found = [
        item
        for item in hits(frontend_sources(), r"https?://")
        if not item.split(":")[0].startswith("web/src/api/")
        and "/__tests__/" not in item.split(":")[0]
        and "www.w3.org/" not in item
    ]
    assert found == [], f"前端在 api/ 之外直连了第三方：{found}"


def test_frontend_sources_exist():
    """A12 是空集合断言，目录不存在时会假通过——这里把前提钉住。"""
    assert frontend_sources(), "web/src 下没有前端源码"


# ---------------- A13：agent → security 的边界（判据：策略细节不进调度层） ----------------


def policy_imports(path: Path) -> tuple[set[str], set[str]]:
    """返回 (运行时 import 的 security 模块, 只在 TYPE_CHECKING 下 import 的)。

    用 AST 而不是 grep：判据特意区分"注解用的惰性 import"与"真依赖"，
    正则分不出来，而这条边界的价值恰恰在那个区分上。
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
                    # `from ..security import permission` 也把 security.permission 记上：
                    # 包级 import 同样是跨层使用。
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


# agent/ 允许 import security 的文件与各自用到的模块。这不是"豁免名单"，而是把边界
# 写成会失败的断言：调度（loop/run）、装配（context/compaction）、工具协议（execution）、
# 终止（stop）与 spec 必须零安全层运行时依赖；只有 state（持有 RunSecurity 实例）与
# hooks（注册权限裁决默认回调）两条边。policy 拆包后压缩/提示词/待办/技能加载器都
# 住在 agent 内部，不再跨层。注解里的 RunSecurity / ApprovalLedger 只许在 TYPE_CHECKING
# 下出现。
AGENT_SECURITY_EDGES: dict[str, set[str]] = {
    "avid/agent/state.py": {"security.permission"},
    "avid/agent/hooks.py": {"security.permission"},
    # tools 取 security 的两个默认值常量（审批放行标记 / 权限模式缺省）
    "avid/agent/tools/files.py": {"security.permission"},
    "avid/agent/tools/subagent.py": {"security.permission"},
}
SECURITY_FREE_AGENT = (
    "avid/agent/execution.py",
    "avid/agent/spec.py",
    "avid/agent/run.py",
    "avid/agent/stop.py",
    "avid/agent/context.py",
    "avid/agent/compaction.py",
    "avid/agent/todo.py",
    "avid/agent/prompt.py",
    "avid/agent/skills.py",
)


def test_a13_scheduling_and_tools_have_zero_runtime_security_dependency():
    for name in SECURITY_FREE_AGENT:
        runtime, _typing = policy_imports(ROOT / name)
        assert runtime == set(), f"{name} 出现了对安全层的运行时依赖：{sorted(runtime)}"


def test_a13_agent_security_edges_are_exactly_the_declared_ones():
    """agent 对 security 的每一条运行时 import 都必须是申报过的边（新增先改这里）。"""
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
    """注解用的 import 必须是惰性的：RunSecurity / ApprovalLedger / McpManager 只在
    TYPE_CHECKING 下出现，"零运行时依赖"不是因为名字没出现，而是 import 真没执行。
    """
    for name in (
        "avid/agent/spec.py",
        "avid/agent/run.py",
        "avid/agent/stop.py",
    ):
        _runtime, typing_only = policy_imports(ROOT / name)
        assert typing_only <= {"security.permission"}, f"{name}: {sorted(typing_only)}"


def test_web_imports_name_submodules_not_the_package():
    """`web/` 内部不许用 `from . import <子模块>` 的形式（P3-15）。

    `web/__init__.py` 会 import `app`，`app` 会 import 各个路由模块；路由再写
    `from .. import sse`，静态依赖图里就等于"回头 import 包"，于是出现
    `web ↔ app ↔ routes.events` 的环。`from ..sse import X` 表达的是对子模块的依赖，
    方向清楚。

    注意 `from . import current_services` **不算**：`current_services` 是路由包
    `__init__` 导出的函数（名字），不是子模块。所以这里按 AST 判断被导入的名字是否
    对应真实存在的模块文件/子包——只看语法会把这条合法用法一起误伤。
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
