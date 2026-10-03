"""批内并行：分段、真重叠、源顺序、屏障、失败隔离、取消、与串行等价（阶段 25）。

规则（已确认）：

* 默认并发（上限 10，`AVID_MAX_PARALLEL_TOOL_CALLS` 可配）；`max_parallel=1` = 旧串行。
* 按工具分类：**并发安全**的调用聚成一段一起跑；**独占**调用单独跑并充当屏障。
* 结果永远按 assistant 源顺序返回，与完成顺序无关。
* 单个调用失败/被拒不影响同批其余（沿用"工具失败回文本、不中断循环"）。
* 取消后不再派发尚未开始的调用；未执行的调用回一条**真实**的"未执行"文本
  （不回它会让 assistant 消息里留下没有回应的 tool_calls，transcript 结构不合法）。
* 同批内完全相同的调用**不去重**（各自执行、各自结果）。

"真重叠"的判定用 `threading.Barrier(2)`：两个 handler 不并发就会等超时，
测试据此把"看起来并行"与"真的并行"区分开。
"""

from __future__ import annotations

import threading
import time

import pytest

from avid.agent.execution import (
    CANCELLED_CONTENT,
    ToolOutcome,
    execute_batch,
    execute_one,
    plan_segments,
)
from avid.agent.state import RunState


def call(name: str, arguments: str = "{}", call_id: str = "c1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def make_state() -> RunState:
    """本文件只测调度：挂一份空 hook 注册表。

    默认注册表里有权限 hook（`write_file`/`bash` 会发起审批并读 stdin）与截断 hook，
    那是别的用例的题目；这里要观察的是"谁和谁同时在跑"，不该被审批阻塞。
    """
    from avid.agent.hooks import HookRegistry

    return RunState(hooks=HookRegistry())


class Recorder:
    """记录每次 handler 的进入/退出时刻，用来判定两次调用是否真的重叠。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.spans: list[tuple[str, float, float]] = []

    def handler(
        self,
        label: str,
        *,
        delay: float = 0.0,
        barrier: threading.Barrier | None = None,
        boom: Exception | None = None,
        on_call=None,
    ):
        def run(arguments, **kwargs):
            start = time.monotonic()
            if on_call is not None:
                on_call()
            try:
                if barrier is not None:
                    barrier.wait(timeout=3)  # 串行执行时这里会超时 → 测试红
                if delay:
                    time.sleep(delay)
                if boom is not None:
                    raise boom
                return f"{label} ok"
            finally:
                end = time.monotonic()
                with self._lock:
                    self.spans.append((label, start, end))

        return run

    def finish_order(self) -> list[str]:
        return [label for label, _, _ in sorted(self.spans, key=lambda item: item[2])]

    def span(self, label: str) -> tuple[float, float]:
        for name, start, end in self.spans:
            if name == label:
                return start, end
        raise AssertionError(f"没有记录到 {label}")

    def overlaps(self, left: str, right: str) -> bool:
        a_start, a_end = self.span(left)
        b_start, b_end = self.span(right)
        return a_start < b_end and b_start < a_end


# ---------------- 分段（纯函数） ----------------


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (10, [[0], [1], [2, 3]]),
        (2, [[0], [1], [2, 3]]),
        (1, [[0], [1], [2], [3]]),
    ],
)
def test_plan_segments_isolates_exclusive_calls(limit, expected):
    """独占调用必须**单独成段**：它是屏障，不能被塞进并发段里。"""
    calls = [
        call("read_file", call_id="c1"),
        call("write_file", call_id="c2"),
        call("glob", call_id="c3"),
        call("web_search", call_id="c4"),
    ]

    assert plan_segments(calls, limit) == expected


def test_plan_segments_keeps_every_call_exactly_once():
    calls = [
        call("read_file", call_id="c1"),
        call("bash", call_id="c2"),
        call("glob", call_id="c3"),
        call("web_search", call_id="c4"),
    ]

    flat = [index for group in plan_segments(calls, 10) for index in group]

    assert flat == [0, 1, 2, 3]


def test_plan_segments_unknown_tool_is_exclusive():
    """没表态的工具按最保守处理：独占。"""
    calls = [call("read_file", call_id="c1"), call("brand_new", call_id="c2")]

    assert plan_segments(calls, 10) == [[0], [1]]


# ---------------- 真重叠 ----------------


def test_safe_calls_in_one_batch_actually_overlap():
    barrier = threading.Barrier(2)
    rec = Recorder()
    registry = {"read_file": rec.handler("read_file", barrier=barrier)}
    batch = [
        call("read_file", '{"path": "a.txt"}', "c1"),
        call("read_file", '{"path": "b.txt"}', "c2"),
    ]

    outcomes = execute_batch(
        batch, state=make_state(), registry=registry, max_parallel=2
    )

    assert [item.tool_call_id for item in outcomes] == ["c1", "c2"]
    assert [item.content for item in outcomes] == ["read_file ok", "read_file ok"]


def test_results_keep_source_order_when_completion_is_reversed():
    rec = Recorder()
    registry = {
        "read_file": rec.handler("slow", delay=0.2),
        "glob": rec.handler("fast"),
    }
    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
    ]

    outcomes = execute_batch(
        batch, state=make_state(), registry=registry, max_parallel=2
    )

    assert [item.tool_call_id for item in outcomes] == ["c1", "c2"]
    assert rec.finish_order() == ["fast", "slow"], "第二条应当先完成（否则没测到乱序）"


# ---------------- 屏障 ----------------


def test_exclusive_call_forms_a_barrier():
    rec = Recorder()
    names = ("read_file", "glob", "write_file", "web_search", "get_task")
    registry = {name: rec.handler(name) for name in names}
    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
        call("write_file", call_id="c3"),
        call("web_search", call_id="c4"),
        call("get_task", call_id="c5"),
    ]

    outcomes = execute_batch(
        batch, state=make_state(), registry=registry, max_parallel=4
    )

    assert [item.tool_call_id for item in outcomes] == ["c1", "c2", "c3", "c4", "c5"]
    # 屏障本身不与任何调用重叠
    for other in ("read_file", "glob", "web_search", "get_task"):
        assert not rec.overlaps("write_file", other), f"write_file 与 {other} 重叠了"
    # 屏障两侧的两个并发段也不互相重叠
    for before in ("read_file", "glob"):
        for after in ("web_search", "get_task"):
            assert not rec.overlaps(before, after), f"{before} 与 {after} 跨屏障重叠了"


def test_consecutive_exclusive_calls_stay_serial():
    rec = Recorder()
    registry = {
        "write_file": rec.handler("w1"),
        "bash": rec.handler("w2"),
    }
    batch = [
        call("write_file", call_id="c1"),
        call("bash", call_id="c2"),
    ]

    execute_batch(batch, state=make_state(), registry=registry, max_parallel=8)

    assert not rec.overlaps("w1", "w2")


# ---------------- 失败隔离 ----------------


def test_one_failure_does_not_stop_the_others():
    rec = Recorder()
    registry = {
        "read_file": rec.handler("bad", boom=RuntimeError("磁盘炸了")),
        "glob": rec.handler("ok"),
    }
    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
    ]

    outcomes = execute_batch(
        batch, state=make_state(), registry=registry, max_parallel=2
    )

    assert "工具执行失败" in outcomes[0].content and "磁盘炸了" in outcomes[0].content
    assert outcomes[1].content == "ok ok"
    assert len(rec.spans) == 2, "失败的调用不该取消同批的另一个"


def test_identical_calls_in_one_batch_are_not_deduplicated():
    """同一段里两个一模一样的调用各自执行（去重会悄悄吞掉第二次的真实结果）。"""
    calls: list[dict] = []

    def handler(arguments, **kwargs):
        calls.append(arguments)
        return "ok"

    batch = [
        call("read_file", '{"path": "same.txt"}', "c1"),
        call("read_file", '{"path": "same.txt"}', "c2"),
    ]

    outcomes = execute_batch(
        batch, state=make_state(), registry={"read_file": handler}, max_parallel=2
    )

    assert calls == [{"path": "same.txt"}, {"path": "same.txt"}]
    assert [item.tool_call_id for item in outcomes] == ["c1", "c2"]


# ---------------- 取消 ----------------


def test_cancelled_batch_skips_calls_not_yet_dispatched():
    state = make_state()
    started: list[str] = []

    def first(arguments, **kwargs):
        started.append("first")
        state.cancel("user")
        return "做完了"

    def second(arguments, **kwargs):
        started.append("second")
        return "不该跑"

    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
    ]

    outcomes = execute_batch(
        batch,
        state=state,
        registry={"read_file": first, "glob": second},
        max_parallel=1,
    )

    assert started == ["first"], "取消后不该再派发新调用"
    assert outcomes[0].content == "做完了"
    assert outcomes[1].content == CANCELLED_CONTENT
    assert len(outcomes) == 2, "条数必须与 tool_calls 对齐，否则 transcript 结构不合法"


def test_cancelled_before_dispatch_produces_only_placeholders():
    """进批之前就已经取消了：一个都不跑，全部回"未执行"。"""
    state = make_state()
    started: list[str] = []

    def slow(arguments, **kwargs):
        started.append("slow")
        time.sleep(0.05)
        return "跑完了"

    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
    ]
    state.cancel("user")

    outcomes = execute_batch(
        batch,
        state=state,
        registry={"read_file": slow, "glob": slow},
        max_parallel=2,
    )

    assert started == [], started
    assert [item.content for item in outcomes] == [CANCELLED_CONTENT, CANCELLED_CONTENT]


def test_cancelled_mid_segment_does_not_abandon_the_calls_in_flight():
    """一段之内取消：同段已在跑的调用照常收尾，**之后的段不再派发**。

    这是"停止派发新调用、不打断在飞的"那条规则的边界：线程杀不掉，假装打断只会让
    结果与事实不符；而没派发的调用必须回一条真实的"未执行"（条数要与 tool_calls 对齐）。
    """
    state = make_state()
    finished: list[str] = []

    def cancelling(arguments, **kwargs):
        time.sleep(0.01)
        finished.append("cancelling")
        state.cancel("user")  # 同段的兄弟还在跑
        return "c1 完成"

    def slow(arguments, **kwargs):
        time.sleep(0.15)
        finished.append("slow")
        return "c2 完成"

    batch = [
        call("read_file", '{"path": "a.txt"}', "c1"),
        call("glob", '{"pattern": "*.txt"}', "c2"),
        call("read_file", '{"path": "b.txt"}', "c3"),
    ]

    outcomes = execute_batch(
        batch,
        state=state,
        registry={"read_file": cancelling, "glob": slow},
        max_parallel=2,
    )

    assert sorted(finished) == ["cancelling", "slow"], "同段的两个都要收尾"
    assert outcomes[0].content == "c1 完成"
    assert outcomes[1].content == "c2 完成", "在飞的兄弟不该被取消丢掉"
    assert outcomes[2].content == CANCELLED_CONTENT, "之后的段不再派发"


# ---------------- 与串行等价 ----------------


def test_max_parallel_one_matches_the_serial_reference_byte_for_byte():
    """max_parallel=1 必须与旧的"逐个 execute_one"完全同结果，且不重叠。"""
    rec = Recorder()
    registry = {
        "read_file": rec.handler("r1"),
        "glob": rec.handler("r2"),
        "write_file": rec.handler("w"),
        "get_task": rec.handler("r3"),
    }
    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
        call("write_file", call_id="c3"),
        call("get_task", call_id="c4"),
    ]

    parallel_one = execute_batch(
        batch, state=make_state(), registry=registry, max_parallel=1
    )

    assert rec.finish_order() == ["r1", "r2", "w", "r3"], "串行档里不该出现任何乱序"

    # 参考实现：改动前的写法——逐个调 execute_one（另用一份记录器，别混进上一跑的时间线）。
    reference_rec = Recorder()
    reference_registry = {
        "read_file": reference_rec.handler("r1"),
        "glob": reference_rec.handler("r2"),
        "write_file": reference_rec.handler("w"),
        "get_task": reference_rec.handler("r3"),
    }
    reference_state = make_state()
    reference = [
        ToolOutcome(
            tool_call_id=item["id"],
            content=execute_one(
                item["function"]["name"],
                item["function"]["arguments"],
                reference_registry,
                state=reference_state,
                round_index=0,
                tool_call_id=item["id"],
            ),
        )
        for item in batch
    ]

    assert [(o.tool_call_id, o.content) for o in parallel_one] == [
        (o.tool_call_id, o.content) for o in reference
    ]
    assert reference_rec.finish_order() == ["r1", "r2", "w", "r3"]


def test_default_is_serial_so_callers_opt_in():
    """不传 max_parallel 时保持旧行为（直调 / bare 路径因此不受影响）。"""
    rec = Recorder()
    registry = {"read_file": rec.handler("only")}

    execute_batch([call("read_file", call_id="c1")], state=make_state(), registry=registry)

    assert rec.finish_order() == ["only"]


# ---------------- 共享状态 ----------------


def test_state_counters_survive_concurrent_updates():
    state = make_state()
    threads = [
        threading.Thread(target=lambda: [state.note_tool_call() for _ in range(500)])
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state.tool_calls == 4000
    assert state.snapshot()["tool_calls"] == 4000


def test_repeat_counter_hands_out_every_number_exactly_once():
    """重复提醒的计数必须原子自增：并发下丢号会让提醒永远到不了阈值。"""
    state = make_state()
    seen: list[int] = []
    lock = threading.Lock()

    def bump():
        for _ in range(200):
            value = state.note_repeat("bash:same")
            with lock:
                seen.append(value)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(seen) == list(range(1, 801))
