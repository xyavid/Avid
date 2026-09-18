"""A1 – A6 / A10 – A12：架构边界用 grep 与断言守住（不依赖运行）。

这些规则的价值在于它们**会失败**：一次「顺手 import 一下」会被立刻拦住。
边界是正则的边界——它只匹配字面量，拼接出来的 URL 与间接 import 不在覆盖内
（设计文档 §3.4 已写明这条限制）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "avid"
WEB = ROOT / "web"

KERNEL_PACKAGES = ("ai", "runtime", "policy", "session", "tools")


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


# ---------------- A3 ----------------


def test_a3_loop_is_still_only_a_scheduler():
    loop = (SRC / "runtime" / "loop.py").read_text(encoding="utf-8")
    assert loop.count("trigger_hooks(") == 2, "循环只该有 UserPromptSubmit 与 Stop 两个 hook 调用点"
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
    found = [
        item
        for item in hits(frontend_sources(), r"https?://")
        if not item.split(":")[0].startswith("web/src/api/")
    ]
    assert found == [], f"前端在 api/ 之外直连了第三方：{found}"


def test_frontend_sources_exist():
    """A12 是空集合断言，目录不存在时会假通过——这里把前提钉住。"""
    assert frontend_sources(), "web/src 下没有前端源码"
