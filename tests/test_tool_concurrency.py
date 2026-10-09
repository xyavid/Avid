"""并发分类：三档声明、单向降级、bash 的只读判定。

判据表就写在这个文件的参数化用例里——「什么算安全」是一份会被改的名单，
它和它的用例必须待在一起（否则下一次放宽会没人拦）。
"""

from __future__ import annotations

import threading
import time

import pytest

from avid.agent.execution import execute_batch, plan_segments
from avid.agent.state import RunState
from avid.agent.tools import specs
from avid.agent.tools.registry import ToolSpec
from avid.agent.tools.safety import is_concurrency_safe
from avid.security.command_parse import is_read_only

# ---------------- bash：什么算纯读 ----------------


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la src",
        "pwd",
        "cat README.md",
        "head -n 20 src/app.py",
        "tail -f log.txt".replace("-f ", "-n 5 "),
        "wc -l src/app.py",
        "rg TODO src",
        "grep -rn '会话' .",
        "git status",
        "git diff --stat",
        "git log --oneline -5",
        "cat a.py | grep def",
        "ls src && ls tests",
        "stat pyproject.toml",
    ],
)
def test_commands_that_only_read(command):
    assert is_read_only(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "ls > files.txt",
        "cat a.py >> all.py",
        "echo hi > out.txt",
        "ls | tee out.txt",
        "ls; rm x",
        "ls && touch marker",
        "git commit -m x",
        "git push",
        "git checkout main",
        "sed -i s/a/b/ f.py",
        "awk '{print > \"f\"}'",
        "find . -delete",
        "find . -exec rm {} ;",
        "python -c 'open(\"f\",\"w\")'",
        "curl https://example.com",
        "unknown_binary --flag",
        "sudo ls",
        "ls $(rm x)",
        "",
    ],
)
def test_commands_that_are_not_provably_read_only(command):
    """保守优先：拿不准就交给独占（宁可少并行，不可让写混进并行段）。"""
    assert is_read_only(command) is False


# ---------------- 三档声明与单向降级 ----------------


def test_declared_classes_are_three_way():
    classes = {spec.concurrency for spec in specs()}
    assert classes <= {"safe", "exclusive", "conditional"}
    assert "conditional" in classes  # bash 这一档要真用上


def test_unknown_tool_is_exclusive():
    assert is_concurrency_safe("never_registered_tool", {"x": 1}) is False


def test_a_conditional_tool_decides_by_its_arguments():
    assert is_concurrency_safe("bash", {"command": "ls"}) is True
    assert is_concurrency_safe("bash", {"command": "rm -rf x"}) is False


def test_an_assessor_that_raises_falls_back_to_exclusive(monkeypatch):
    from avid.agent.tools import safety

    broken = ToolSpec(
        name="flaky",
        description="",
        parameters={},
        impl=lambda args: "x",
        concurrency="conditional",
        stateful=False,
        writes=False,
        assess=lambda arguments, state: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(safety, "_BY_NAME", {**safety._BY_NAME, "flaky": broken})

    assert is_concurrency_safe("flaky", {}) is False


def test_a_writing_tool_can_never_be_conditional():
    """声明会写的工具不许拿到 conditional：降级是一处误判面，别开在写路径上。"""
    from avid.agent.tools.registry import tool

    with pytest.raises(TypeError, match="会写文件"):

        @tool(
            name="dangerous",
            description="",
            properties={},
            concurrency="conditional",
            writes=True,
            assess=lambda arguments, state: "safe",
        )
        def dangerous(args):  # pragma: no cover - 声明期就报错
            return "x"


def test_conditional_requires_an_assessor():
    from avid.agent.tools.registry import tool

    with pytest.raises(TypeError, match="assess"):

        @tool(name="noassess", description="", properties={}, concurrency="conditional")
        def noassess(args):  # pragma: no cover - 声明期就报错
            return "x"


# ---------------- 分段：只读命令加入并行段 ----------------


def call(name: str, arguments: dict, *, call_id: str = "c") -> dict:
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_a_read_only_bash_joins_the_parallel_segment():
    calls = [
        call("bash", {"command": "ls"}),
        call("read_file", {"path": "a.py"}),
        call("bash", {"command": "rm -rf build"}),
        call("grep_search", {"pattern": "x"}),
    ]

    assert plan_segments(calls, 8) == [[0, 1], [2], [3]]


def test_a_write_command_stays_a_barrier():
    calls = [call("bash", {"command": "git status"}), call("bash", {"command": "git commit -m x"})]

    assert plan_segments(calls, 8) == [[0], [1]]


def test_unparseable_arguments_are_treated_as_exclusive():
    broken = {"id": "c", "type": "function", "function": {"name": "bash", "arguments": "{不是 JSON"}}

    assert plan_segments([broken, call("read_file", {"path": "a.py"})], 8) == [[0], [1]]


# ---------------- 墙钟：真的更快（否则「并行」只是感觉） ----------------


def _sleep_state() -> RunState:
    from avid.agent.hooks import HookRegistry

    return RunState(hooks=HookRegistry())


def test_read_only_bash_calls_share_one_wall_clock_slot():
    """三个只读命令（各睡 0.3 秒）：并行应当 ≈ 一个时段，而不是三个。"""
    registry = {
        "bash": lambda args, **kwargs: (time.sleep(0.3), "ok")[1],
    }
    calls = [call("bash", {"command": "ls"}, call_id=f"c{index}") for index in range(3)]

    started = time.monotonic()
    execute_batch(calls, state=_sleep_state(), registry=registry, max_parallel=3)
    parallel = time.monotonic() - started

    started = time.monotonic()
    execute_batch(calls, state=_sleep_state(), registry=registry, max_parallel=1)
    serial = time.monotonic() - started

    assert parallel < serial * 0.6, f"并行 {parallel:.2f}s vs 串行 {serial:.2f}s"


def test_a_write_command_does_not_join_the_reads():
    """写命令夹在中间：它自己独占，前后的读仍在各自的段里并行（但跨越它的不重叠）。"""
    spans: list[tuple[str, float, float]] = []
    lock = threading.Lock()

    def slow_read(args, **kwargs):
        start = time.monotonic()
        time.sleep(0.2)
        with lock:
            spans.append(("read", start, time.monotonic()))
        return "ok"

    def write(args, **kwargs):
        start = time.monotonic()
        time.sleep(0.2)
        with lock:
            spans.append(("write", start, time.monotonic()))
        return "ok"

    registry = {"bash": lambda args, **kwargs: write(args, **kwargs), "read_file": slow_read}
    calls = [
        call("read_file", {"path": "a.py"}, call_id="r1"),
        call("bash", {"command": "rm -rf x"}, call_id="w"),
        call("read_file", {"path": "b.py"}, call_id="r2"),
    ]

    outcomes = execute_batch(calls, state=_sleep_state(), registry=registry, max_parallel=8)

    assert [item.tool_call_id for item in outcomes] == ["r1", "w", "r2"]
    write_span = next(span for name, *span in spans if name == "write")
    for name, start, end in spans:
        if name == "read":
            assert end <= write_span[0] + 0.05 or start >= write_span[1] - 0.05, "读与写重叠了"
