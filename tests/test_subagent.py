import threading
import time

import pytest

from avid.agent.state import RunState
from avid.agent.tools import SUB_HANDLERS, SUB_TOOLS, TOOLS
from avid.agent.tools.subagent import (
    MAX_PARALLEL,
    SUB_SYSTEM,
    TASK_FIELDS,
    TASK_STOP_LINE,
    run_subagent,
    subagent,
    task_brief,
)
from avid.agent.tools.validate import validate_arguments
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")

SUBAGENT_PARAMETERS = next(
    item["function"]["parameters"]
    for item in TOOLS
    if item["function"]["name"] == "subagent"
)


def run(payload, **kwargs):
    """Call the tool with a RunState, as execution (the real caller) does."""
    return subagent(payload, state=RunState(), **kwargs)


def task(
    description="干点活",
    objective="把这件事做完",
    scope="整个仓库，但不要改文件",
    context="父对话里已经确认的现象",
    constraints="只读；不要提交",
    deliverable="结论摘要：文件、根因、建议",
):
    return {
        "description": description,
        "objective": objective,
        "scope": scope,
        "context": context,
        "constraints": constraints,
        "deliverable": deliverable,
    }


# ---- tool set and recursion guard ----


def test_sub_tools_exclude_subagent():
    names = {item["function"]["name"] for item in SUB_TOOLS}
    all_names = {item["function"]["name"] for item in TOOLS}

    assert "subagent" not in names
    assert names == all_names - {"subagent"}
    assert names == set(SUB_HANDLERS)


def test_subagent_has_no_turn_cap_of_its_own():
    """A subagent has no turn cap of its own: the kernel has no such limit."""
    import avid.agent.tools.subagent as sub

    assert not hasattr(sub, "SUBAGENT_MAX_TURNS")


def test_sub_system_asks_for_a_self_contained_summary():
    assert "摘要" in SUB_SYSTEM
    assert "不要反问" in SUB_SYSTEM
    # The system prompt must say the user message is a task brief, not free text
    assert "Deliverable" in SUB_SYSTEM


def test_task_fields_are_the_six_fields_in_render_order():
    """Field name is section name and the order is the render order; schema and validation
    derive from this table."""
    assert [key for key, _ in TASK_FIELDS] == [
        "description",
        "objective",
        "scope",
        "context",
        "constraints",
        "deliverable",
    ]
    assert all(hint.strip() for _, hint in TASK_FIELDS)


# ---- argument validation ----


def test_rejects_empty_tasks():
    assert "非空数组" in run({"tasks": []})


def test_rejects_non_list():
    assert "非空数组" in run({"tasks": "不是数组"})


def test_rejects_too_many_tasks():
    tasks = [task(f"第{i}", "p") for i in range(MAX_PARALLEL + 1)]

    result = run({"tasks": tasks})

    assert f"最多派发 {MAX_PARALLEL} 个" in result
    assert f"收到 {MAX_PARALLEL + 1} 个" in result


def test_rejects_a_task_missing_a_section():
    result = run({"tasks": [{"description": "只有标题"}]})

    assert "objective 不能为空" in result


def test_rejects_the_free_text_task_shape():
    """A parent cannot send free text: missing any of the six sections is rejected with a hint."""
    result = run({"tasks": [{"description": "标题", "prompt": "把这件事做完"}]})

    assert "objective 不能为空" in result
    assert "这次要达成什么" in result


def test_rejects_blank_description():
    result = run({"tasks": [task(description="   ")]})

    assert "description 不能为空" in result


def test_schema_requires_every_section():
    """Schema validation runs before the implementation: a missing section is rejected at
    the execution step."""
    problem = validate_arguments(SUBAGENT_PARAMETERS, {"tasks": [{"description": "标题"}]})

    assert problem is not None
    assert "objective" in problem and "必填" in problem


def test_rejects_non_object_item():
    assert "不是对象" in run({"tasks": ["字符串"]})


def test_validation_happens_before_any_subagent_runs():
    started = []

    run(
        {"tasks": [task("好的"), {"description": "坏的"}]},
        runner=lambda prompt, **kwargs: started.append(prompt) or "x",
    )

    assert started == []


# ---- task brief rendering ----


def test_child_receives_the_rendered_brief():
    """The child's first message is a fixed harness-rendered skeleton (title line, five sections,
    stop line), not the parent's free text."""
    seen = []

    run({"tasks": [task()]}, runner=lambda prompt, **kwargs: seen.append(prompt) or "ok")

    assert seen == [
        "干点活\n\n"
        "Objective:\n把这件事做完\n\n"
        "Scope:\n整个仓库，但不要改文件\n\n"
        "Context:\n父对话里已经确认的现象\n\n"
        "Constraints:\n只读；不要提交\n\n"
        "Deliverable:\n结论摘要：文件、根因、建议\n\n"
        + TASK_STOP_LINE
    ]


def test_task_brief_closes_with_the_stop_line():
    """The harness supplies the stop line: stop when done and hand the conclusion back."""
    brief = task_brief(task())

    assert brief.endswith(TASK_STOP_LINE)
    assert "parent agent" in TASK_STOP_LINE


def test_task_brief_keeps_the_sections_in_order():
    """Section order is read order: rendering keys off field names, so the parent's order
    cannot scramble it."""
    brief = task_brief(task(scope="只看 avid/agent/", deliverable="三段话结论"))

    assert brief.index("Objective:") < brief.index("Scope:")
    assert brief.index("Scope:") < brief.index("Context:")
    assert brief.index("Context:") < brief.index("Constraints:")
    assert brief.index("Constraints:") < brief.index("Deliverable:")
    assert "只看 avid/agent/" in brief
    assert "三段话结论" in brief
    assert brief.startswith("干点活\n\nObjective:")


# ---- parallel dispatch and summary ----


def test_runs_every_task_and_labels_the_results():
    seen = []

    def runner(prompt, *, config, auto_approve, ask=None, **kwargs):
        seen.append(prompt)
        return f"摘要：{prompt}"

    result = run(
        {
            "tasks": [
                task("统计行数", "统计 src 下各文件行数"),
                task("检查测试", "列出所有测试文件"),
            ]
        },
        runner=runner,
    )

    assert [brief.splitlines()[0] for brief in seen] == ["统计行数", "检查测试"]
    assert "Objective:\n统计 src 下各文件行数" in seen[0]
    assert "Objective:\n列出所有测试文件" in seen[1]
    assert result.startswith("已并行运行 2 个 subagent：")
    assert "=== 1/2 · 统计行数 ===" in result
    assert "=== 2/2 · 检查测试 ===" in result
    assert "摘要：统计行数" in result


def test_single_task_uses_singular_wording():
    result = run(
        {"tasks": [task()]}, runner=lambda prompt, **kwargs: "ok"
    )

    assert result.startswith("已运行 1 个 subagent：")


def test_empty_summary_becomes_no_summary():
    result = run(
        {"tasks": [task()]}, runner=lambda prompt, **kwargs: "   "
    )

    assert "(no summary)" in result


def test_one_failure_does_not_lose_the_others():
    def runner(prompt, **kwargs):
        if "炸" in prompt:
            raise RuntimeError("内部错误")
        return "好"

    result = run(
        {"tasks": [task("正常", "好"), task("失败", "炸"), task("也正常", "好")]},
        runner=runner,
    )

    assert "Subagent failed: 内部错误" in result
    assert "=== 2/3 · 失败 ===" in result
    assert result.count("好") >= 2


def test_timeout_is_reported_per_task():
    def runner(prompt, **kwargs):
        time.sleep(1)
        return "太慢"

    result = run({"tasks": [task("慢", "p")]}, runner=runner, timeout=0.05)

    assert "Subagent timed out after" in result


def test_tasks_actually_run_in_parallel():
    """Both tasks must meet at the barrier: serial execution hits the timeout and fails."""
    barrier = threading.Barrier(2, timeout=2)

    def runner(prompt, **kwargs):
        barrier.wait()
        return f"完成 {prompt}"

    result = run(
        {"tasks": [task("a", "a"), task("b", "b")]},
        runner=runner,
    )

    assert "完成 a" in result
    assert "完成 b" in result
    assert "failed" not in result


def test_auto_approve_comes_from_the_run_state():
    """The auto-approve switch is read from RunState and passed explicitly to the child run."""
    seen = []

    def runner(prompt, *, auto_approve, config, ask=None, **kwargs):
        seen.append(auto_approve)
        return "ok"

    subagent({"tasks": [task()]}, state=RunState(auto_approve=True), runner=runner)

    assert seen == [True]


def test_auto_approve_defaults_to_false(monkeypatch):
    seen = []

    def runner(prompt, *, auto_approve, config, ask=None, **kwargs):
        seen.append(auto_approve)
        return "ok"

    run({"tasks": [task()]}, runner=runner)

    assert seen == [False]


# ---------- run_subagent ----------


def test_run_subagent_puts_the_task_brief_in_the_first_message(monkeypatch):
    """run_subagent only puts the given brief into the first user message; the skeleton is
    rendered in the subagent layer."""
    import dataclasses

    from avid.agent import run as run_module

    seen = {}

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            seen["messages"] = messages
            seen["spec"] = spec
            seen.update(kwargs)

        def run(self):
            class _Outcome:
                text = "摘要"

            return _Outcome()

    monkeypatch.setattr(run_module, "Run", FakeRun)

    assert run_subagent("任务原文", config=CONFIG) == "摘要"
    assert seen["messages"] == [{"role": "user", "content": "任务原文"}]
    assert seen["spec"].instructions == SUB_SYSTEM
    assert seen["spec"].tools is SUB_TOOLS
    assert seen["spec"].registry is SUB_HANDLERS
    # No round budget is passed: the kernel has no such concept
    assert "max_rounds" not in {f.name for f in dataclasses.fields(seen["spec"])}


def test_run_subagent_returns_no_summary_for_empty_text(monkeypatch):
    from avid.agent import run as run_module

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            pass

        def run(self):
            class _Outcome:
                text = "   "

            return _Outcome()

    monkeypatch.setattr(run_module, "Run", FakeRun)

    assert run_subagent("随便", config=CONFIG) == "(no summary)"


def test_run_subagent_streams_its_words_onto_the_observer(monkeypatch):
    """A child run streams by default; the parent wraps its observer to tag events with
    {task, index} before forwarding."""
    from avid.agent import run as run_module
    from avid.agent.tools import subagent as subagent_module

    seen: list[tuple[str, str]] = []
    specs: list[object] = []

    def fake_stream(config, messages, *, on_delta=None, on_reasoning=None, **kwargs):
        on_reasoning("先看一眼")
        on_delta("看完了")
        return object()

    monkeypatch.setattr(subagent_module, "stream_completion", fake_stream)

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            specs.append(spec)

        def run(self):
            # use the spec's chat, the default run_subagent installed
            specs[-1].chat(None, [{"role": "user", "content": "x"}])

            class _Outcome:
                text = "摘要"

            return _Outcome()

    monkeypatch.setattr(run_module, "Run", FakeRun)

    assert run_subagent("任务原文", config=CONFIG, observer=seen.append) == "摘要"

    assert [(e.type, e.data["text"]) for e in seen] == [
        ("reasoning_delta", "先看一眼"),
        ("assistant_delta", "看完了"),
    ]
    # Summarize bypasses streaming, or summary text would mix into the child's body
    assert specs[0].summarize is subagent_module.chat_completion


def test_run_subagent_uses_an_injected_chat_as_is(monkeypatch):
    """An injected chat is used as is (scripted models in tests take this path)."""
    from avid.agent import run as run_module

    specs: list[object] = []

    def scripted(config, messages, **kwargs):
        return object()

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            specs.append(spec)

        def run(self):
            class _Outcome:
                text = "摘要"

            return _Outcome()

    monkeypatch.setattr(run_module, "Run", FakeRun)

    assert run_subagent("任务原文", config=CONFIG, chat=scripted) == "摘要"
    assert specs[0].chat is scripted


# ---- cancellation, event nesting and usage accounting ----


def test_check_cancelled_consults_the_external_probe():
    from avid.agent.run import RunCancelled

    state = RunState(cancel_probe=lambda: "外部要求停止")

    with pytest.raises(RunCancelled, match="外部要求停止"):
        state.check_cancelled()


def test_probe_none_means_no_external_source():
    RunState().check_cancelled()  # does not raise


def test_adopt_child_usage_sums_tokens_and_counts_calls():
    from avid.providers.usage import Usage

    parent = RunState()
    child = RunState()
    child.record_usage(Usage(5, 2, 7))
    child.record_usage(Usage(1, 1, 2))

    parent.adopt_child_usage(child)

    assert parent.child_tokens == 9
    assert parent.child_calls == 1
    assert parent.tokens == 9  # total run cost = main loop + subagents


def test_usage_report_carries_subagent_totals():
    from avid.providers.usage import Usage

    parent = RunState()
    assert parent.usage_report()["subagent"] == {"calls": 0, "tokens": 0}

    child = RunState()
    child.record_usage(Usage(5, 2, 7))
    parent.adopt_child_usage(child)

    assert parent.usage_report()["subagent"] == {"calls": 1, "tokens": 7}


def test_run_subagent_wires_probe_observer_and_state(monkeypatch):
    """run_subagent builds the child RunState itself: probe and observer land on it, and the
    reference goes to on_state."""
    from avid.agent import run as run_module
    from avid.agent.run import RunCancelled

    seen = {}

    class FakeRun:
        def __init__(self, messages, spec, *, state=None, **kwargs):
            seen["state"] = state
            self._state = state

        def run(self):
            self._state.check_cancelled()  # the probe fires here
            return "不该到这里"

    monkeypatch.setattr(run_module, "Run", FakeRun)

    kept = []
    with pytest.raises(RunCancelled, match="父已取消"):
        run_subagent(
            "任务",
            config=CONFIG,
            observer=lambda event: None,
            cancel_probe=lambda: "父已取消",
            on_state=kept.append,
        )

    assert kept and kept[0] is seen["state"]
    assert seen["state"].cancel_probe() == "父已取消"


def test_subagent_tags_child_events_and_adopts_usage(monkeypatch):
    """Child events enter the parent stream tagged subagent; on finish the child's tokens
    join the parent ledger."""
    import avid.agent.events as runtime_events
    from avid.agent import run as run_module
    from avid.providers.usage import Usage

    class FakeRun:
        def __init__(self, messages, spec, *, state=None, **kwargs):
            self._state = state

        def run(self):
            self._state.record_usage(Usage(5, 2, 7))
            self._state.emit(runtime_events.RUN_STATUS, round=1, tokens=7, activity="model")

            class _Outcome:
                text = "ok"

            return _Outcome()

    monkeypatch.setattr(run_module, "Run", FakeRun)

    parent = RunState()
    collected = []
    parent.observer = collected.append

    result = subagent({"tasks": [task("统计", "数一下")]}, state=parent)

    assert "摘要" in result or "ok" in result
    tagged = [event for event in collected if event.data.get("subagent")]
    assert tagged, "子事件必须带 subagent 标记"
    assert tagged[0].data["subagent"] == {"task": "统计", "index": 0}
    assert parent.child_tokens == 7
    assert parent.child_calls == 1


def test_parent_cancel_surfaces_quickly(monkeypatch):
    """After a parent cancel the collect converges early instead of waiting for the slow child."""
    from avid.agent import run as run_module

    started = threading.Event()

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            pass

        def run(self):
            started.set()
            time.sleep(5)  # far longer than the test's patience
            return "太慢"

    monkeypatch.setattr(run_module, "Run", FakeRun)

    parent = RunState()

    def cancel_soon():
        assert started.wait(2)
        time.sleep(0.05)
        parent.cancel("用户停止")

    threading.Thread(target=cancel_soon, daemon=True).start()

    began = time.monotonic()
    result = subagent({"tasks": [task("慢", "p")]}, state=parent)
    elapsed = time.monotonic() - began

    assert elapsed < 3, f"取消后 {elapsed:.1f}s 才返回"
    assert "取消" in result


def test_deadline_reaches_child_checkpoints(monkeypatch):
    """On deadline the timeout text returns and the child stops at its next checkpoint."""
    from avid.agent import run as run_module
    from avid.agent.run import RunCancelled

    raised = []

    class FakeRun:
        def __init__(self, messages, spec, *, state=None, **kwargs):
            self._state = state

        def run(self):
            time.sleep(0.3)  # past the timeout
            try:
                self._state.check_cancelled()
            except RunCancelled as exc:
                raised.append(str(exc))
                raise
            return "不该到这里"

    monkeypatch.setattr(run_module, "Run", FakeRun)

    result = run({"tasks": [task("慢", "p")]}, timeout=0.1)
    assert "timed out" in result

    for _ in range(50):  # wait for the orphan thread to reach the checkpoint
        if raised:
            break
        time.sleep(0.05)
    assert raised and "墙钟" in raised[0]
