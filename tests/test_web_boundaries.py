"""A1 – A6 / A10 – A12：架构边界用 grep 与断言守住（不依赖运行）。

这些规则的价值在于它们**会失败**：一次「顺手 import 一下」会被立刻拦住。
边界是正则的边界——它只匹配字面量，拼接出来的 URL 与间接 import 不在覆盖内
（设计文档 §3.4 已写明这条限制）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "avid"
WEB = ROOT / "web"

# svc/ 也在内核侧：它是最容易被"顺手 import 一下 pydantic"的层（离传输层最近），
# 而 A1 以前只查这五个包——`pyproject.toml` 那句"web/ 是唯一 importer"因此少了
# 一半的守护（审查里的 P2-22）。
KERNEL_PACKAGES = ("ai", "runtime", "policy", "session", "tools", "svc")


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
    # 判定按设计文档原文：`grep -rln "fastapi" src/avid` 必须全部落在 web/。
    # （`avid web` 子命令在 cli.py 里 import uvicorn 是文档指定的接线点。）
    found = hits(files_under(suffix=".py"), r"\bfastapi\b")
    assert found, "应该至少有一处 fastapi"
    offenders = [item for item in found if "/web/" not in item.split(":")[0]]
    assert offenders == [], f"内核里出现了 HTTP 框架：{offenders}"


def test_a2_uvicorn_is_only_used_for_the_web_subcommand():
    found = hits(files_under(suffix=".py"), r"^\s*(import|from)\s+uvicorn")
    assert {item.split(":")[0] for item in found} == {"src/avid/cli.py"}


def test_a1_svc_is_also_checked_for_web_framework_imports():
    """A1 的包清单必须含 svc（P2-22）：它离传输层最近，最容易顺手 import pydantic。"""
    assert "svc" in KERNEL_PACKAGES
    assert hits(files_under("svc"), r"fastapi|pydantic|starlette|uvicorn") == []


# ---------------- A3 ----------------


def test_a3_loop_is_still_only_a_scheduler():
    loop = (SRC / "runtime" / "loop.py").read_text(encoding="utf-8")
    assert loop.count("state.hooks.trigger(") == 2, (
        "循环只该有 UserPromptSubmit 与 Stop 两个 hook 调用点"
    )
    assert "while " not in loop, "循环里不该出现手写 while"

    # 没有第二份 agent 循环：agent_loop 的调用点固定为「三个接线点 + 自身定义」。
    # （cli.py 与 svc/runs.py 是内核的两个平级调用方，subagent 复用同一循环。）
    callers = {
        item.split(":")[0]
        for item in code_hits(files_under(suffix=".py"), r"agent_loop\(")
    }
    assert callers == {
        "src/avid/runtime/loop.py",
        "src/avid/cli.py",
        "src/avid/svc/runs.py",
        "src/avid/tools/subagent.py",
    }, callers

    # svc / web 不按轮次自己推进调度（while/for round）
    assert hits(files_under("svc") + files_under("web"), r"for round|while .*round") == []


def test_a3_observation_points_are_declared_once():
    loop = (SRC / "runtime" / "loop.py").read_text(encoding="utf-8")
    assert "on_message" in loop and "on_event" in loop


# ---------------- A4 / A5 ----------------


def test_a4_svc_does_not_import_web():
    assert hits(files_under("svc"), r"avid\.web|from \.\.web") == []


def test_a5_svc_only_maps_kernel_errors():
    found = hits(files_under("svc"), r"RoundLimitExceeded|LLMError")
    # 只允许出现"捕获并映射"的地方：runs.py 的 except 分支
    assert found, "svc 应当显式把内核异常映射成 run_failed"
    for item in found:
        assert "svc/runs.py" in item, item


# ---------------- A6 ----------------


def test_a6_event_names_are_single_sourced():
    """事件名字面量只允许出现在 events.py（其余地方必须用 events.XXX 常量）。"""
    names = ("run_started", "run_finished", "tool_call_started", "tool_call_finished")
    pattern = "|".join(f'"{name}"' for name in names)
    found = [
        item
        for item in hits(files_under(suffix=".py"), pattern)
        if "runtime/events.py" not in item.split(":")[0]
    ]
    assert found == [], f"事件名字面量泄漏到 events.py 之外：{found}"


def test_a10_on_message_wiring_stays_in_four_places():
    found = {
        item.split(":")[0]
        for item in hits(files_under(suffix=".py"), r"on_message")
    }
    assert found == {
        "src/avid/runtime/loop.py",
        "src/avid/session/recorder.py",
        "src/avid/cli.py",
        "src/avid/svc/runs.py",
    }, found


# ---------------- A11 ----------------


def test_a11_recorder_remains_the_only_session_writer():
    assert hits(files_under("web"), r"append_message|\.commit\(") == []
    assert hits(files_under("svc"), r"append_message|\.commit\(") == []


# ---------------- A12 ----------------


def test_a12_frontend_has_no_third_party_urls_outside_api():
    # `__tests__/` 例外：URL 夹具（例如 sanitizeUrl 的用例）必须拿真实字面量当输入，
    # 而它们不产生请求。规则拦的是运行时代码里的第三方端点。
    found = [
        item
        for item in hits(frontend_sources(), r"https?://")
        if not item.split(":")[0].startswith("web/src/api/")
        and "/__tests__/" not in item.split(":")[0]
    ]
    assert found == [], f"前端在 api/ 之外直连了第三方：{found}"


def test_frontend_sources_exist():
    """A12 是空集合断言，目录不存在时会假通过——这里把前提钉住。"""
    assert frontend_sources(), "web/src 下没有前端源码"


# ---------------- A13：runtime → policy 的边界（设计文档 §12 判据 9） ----------------


def policy_imports(path: Path) -> tuple[set[str], set[str]]:
    """返回 (运行时 import 的 policy 模块, 只在 TYPE_CHECKING 下 import 的)。

    用 AST 而不是 grep：判据 9 特意区分"注解用的惰性 import"与"真依赖"，
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
                if module == "policy" or module.startswith("policy."):
                    # `from ..policy import compaction` 也把 policy.compaction 记上：
                    # 包级 import 同样是跨层使用。
                    targets = {
                        f"policy.{alias.name}" for alias in node.names if module == "policy"
                    } | ({module} if module != "policy" else {"policy"})
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


# runtime/ 允许 import policy 的文件与各自用到的模块。这不是"豁免名单"，而是把边界
# 写成会失败的断言：`loop.py` 与 `execution.py` 必须是零运行时依赖（调度与工具协议
# 不该认识策略），其余三个文件各有明确理由——context 编排压缩、state 持有运行期
# 实例、hooks 注册默认回调（设计文档 §12 判据 9 的措辞修正①）。
RUNTIME_POLICY_EDGES: dict[str, set[str]] = {
    "src/avid/runtime/context.py": {"policy", "policy.compaction"},
    "src/avid/runtime/state.py": {
        "policy.permission",
        "policy.skills",
        "policy.todo",
    },
    "src/avid/runtime/hooks.py": {"policy.permission"},
}
POLICY_FREE_RUNTIME = ("src/avid/runtime/loop.py", "src/avid/runtime/execution.py")


def test_a13_loop_and_execution_have_zero_runtime_policy_dependency():
    for name in POLICY_FREE_RUNTIME:
        runtime, _typing = policy_imports(ROOT / name)
        assert runtime == set(), f"{name} 出现了对策略层的运行时依赖：{sorted(runtime)}"


def test_a13_runtime_policy_edges_are_exactly_the_declared_ones():
    """边界是双向的：既不许 loop/execution 反向依赖策略层，也不许别的 runtime
    文件偷偷多出一条没写进设计文档的边（新增一处就必须先改这里与 §12 判据 9）。"""
    for name in POLICY_FREE_RUNTIME:
        assert name not in RUNTIME_POLICY_EDGES

    seen: dict[str, set[str]] = {}
    for path in files_under("runtime"):
        runtime, _typing = policy_imports(path)
        if runtime:
            seen[str(path.relative_to(ROOT))] = runtime

    assert seen == RUNTIME_POLICY_EDGES, (
        "runtime→policy 的实际边与设计文档 §12 判据 9 不一致："
        f"{sorted(set(seen) | set(RUNTIME_POLICY_EDGES))}"
    )


def test_a13_type_checking_imports_stay_inert():
    """注解用的 import 必须是惰性的：`loop.py` 的 AskUser 只在 TYPE_CHECKING 下。

    这条保证"零运行时依赖"不是因为名字没出现，而是因为那段 import 真的没执行。
    """
    runtime, typing_only = policy_imports(SRC / "runtime" / "loop.py")
    assert runtime == set()
    assert typing_only == {"policy.permission"}
