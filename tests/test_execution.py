"""In-batch parallelism: segmentation, real overlap, source order, barriers, failure
isolation, cancellation, and serial equivalence.

Ways the batch scheduler can break, one case each:

* Default concurrency is capped at 10 (`AVID_MAX_PARALLEL_TOOL_CALLS` overrides it);
  `max_parallel=1` is the serial path.
* Concurrent-safe calls form one segment; an exclusive call runs alone and acts as a barrier.
* Results come back in assistant source order, never in completion order.
* One failing or refused call leaves the rest of the batch alone.
* After a cancel, undispatched calls are not started and get a real "not executed" text
  (an unanswered tool_call would make the transcript structurally invalid).
* Identical calls in one batch are not deduplicated.

Real overlap is judged with `threading.Barrier(2)`: handlers that do not run concurrently
time out and the test goes red.
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
    """An empty hook registry for scheduling-only tests: the default permission hook would read
    stdin and block the batch."""
    from avid.agent.hooks import HookRegistry

    return RunState(hooks=HookRegistry())


class Recorder:
    """Records each handler's enter/exit times to tell real overlap from apparent overlap."""

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
                    barrier.wait(timeout=3)  # serial execution times out here and fails
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


# ---- segmentation (pure) ----


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (10, [[0], [1], [2, 3]]),
        (2, [[0], [1], [2, 3]]),
        (1, [[0], [1], [2], [3]]),
    ],
)
def test_plan_segments_isolates_exclusive_calls(limit, expected):
    """An exclusive call must form its own segment: it is a barrier and must never be packed
    into a concurrent one."""
    calls = [
        call("read_file", call_id="c1"),
        call("write_file", call_id="c2"),
        call("glob", call_id="c3"),
        call("load_skill", call_id="c4"),
    ]

    assert plan_segments(calls, limit) == expected


def test_plan_segments_keeps_every_call_exactly_once():
    calls = [
        call("read_file", call_id="c1"),
        call("bash", call_id="c2"),
        call("glob", call_id="c3"),
        call("load_skill", call_id="c4"),
    ]

    flat = [index for group in plan_segments(calls, 10) for index in group]

    assert flat == [0, 1, 2, 3]


def test_plan_segments_unknown_tool_is_exclusive():
    """A tool with no concurrency declaration is treated as exclusive."""
    calls = [call("read_file", call_id="c1"), call("brand_new", call_id="c2")]

    assert plan_segments(calls, 10) == [[0], [1]]


# ---- real overlap ----


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


# ---- barriers ----


def test_exclusive_call_forms_a_barrier():
    rec = Recorder()
    names = ("read_file", "glob", "write_file", "load_skill", "get_task")
    registry = {name: rec.handler(name) for name in names}
    batch = [
        call("read_file", call_id="c1"),
        call("glob", call_id="c2"),
        call("write_file", call_id="c3"),
        call("load_skill", call_id="c4"),
        call("get_task", call_id="c5"),
    ]

    outcomes = execute_batch(
        batch, state=make_state(), registry=registry, max_parallel=4
    )

    assert [item.tool_call_id for item in outcomes] == ["c1", "c2", "c3", "c4", "c5"]
    # The barrier itself overlaps nothing
    for other in ("read_file", "glob", "load_skill", "get_task"):
        assert not rec.overlaps("write_file", other), f"write_file 与 {other} 重叠了"
    # The two concurrent segments on either side do not overlap each other
    for before in ("read_file", "glob"):
        for after in ("load_skill", "get_task"):
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


# ---- failure isolation ----


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
    """Two identical calls in one segment both run; deduplication would swallow the second."""
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


# ---- cancellation ----


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
    """Cancelled before the batch starts: nothing runs, every call gets the placeholder."""
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
    """Cancel inside a segment: in-flight siblings finish, later segments do not dispatch, and
    undispatched calls still get a real placeholder (count must match tool_calls).
    """
    state = make_state()
    finished: list[str] = []

    def cancelling(arguments, **kwargs):
        time.sleep(0.01)
        finished.append("cancelling")
        state.cancel("user")  # a sibling in the same segment is still running
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


# ---- serial equivalence ----


def test_max_parallel_one_matches_the_serial_reference_byte_for_byte():
    """max_parallel=1 must match per-call execute_one byte for byte and never overlap."""
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

    # Reference: call execute_one per item (separate recorder, not mixed into the spans)
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
    """Omitting max_parallel keeps the serial path, so direct callers are unaffected."""
    rec = Recorder()
    registry = {"read_file": rec.handler("only")}

    execute_batch([call("read_file", call_id="c1")], state=make_state(), registry=registry)

    assert rec.finish_order() == ["only"]


# ---- shared state ----


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
    """The repeat counter must increment atomically: a lost number keeps a reminder below
    its threshold forever."""
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
