import threading
import time

import pytest

from avid.agent.state import RunState
from avid.agent.tools import SUB_HANDLERS, SUB_TOOLS, TOOLS
from avid.agent.tools.subagent import (
    MAX_PARALLEL,
    SUB_SYSTEM,
    run_subagent,
    subagent,
)
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def run(payload, **kwargs):
    """带上 RunState 调工具——真实调用方（execution）也是这么传的。"""
    return subagent(payload, state=RunState(), **kwargs)


def task(description="干点活", prompt="把这件事做完"):
    return {"description": description, "prompt": prompt}


# ---------- 工具集与递归防护 ----------


def test_sub_tools_exclude_subagent():
    names = {item["function"]["name"] for item in SUB_TOOLS}
    all_names = {item["function"]["name"] for item in TOOLS}

    assert "subagent" not in names
    assert names == all_names - {"subagent"}
    assert names == set(SUB_HANDLERS)


def test_subagent_has_no_turn_cap_of_its_own():
    """子 agent 不自带轮数上限——内核里已经没有这个限制。"""
    import avid.agent.tools.subagent as sub

    assert not hasattr(sub, "SUBAGENT_MAX_TURNS")


def test_sub_system_asks_for_a_self_contained_summary():
    assert "摘要" in SUB_SYSTEM
    assert "不要反问" in SUB_SYSTEM


# ---------- 参数校验 ----------


def test_rejects_empty_tasks():
    assert "非空数组" in run({"tasks": []})


def test_rejects_non_list():
    assert "非空数组" in run({"tasks": "不是数组"})


def test_rejects_too_many_tasks():
    tasks = [task(f"第{i}", "p") for i in range(MAX_PARALLEL + 1)]

    result = run({"tasks": tasks})

    assert f"最多派发 {MAX_PARALLEL} 个" in result
    assert f"收到 {MAX_PARALLEL + 1} 个" in result


def test_rejects_missing_prompt():
    result = run({"tasks": [{"description": "只有描述"}]})

    assert "prompt 不能为空" in result


def test_rejects_blank_description():
    result = run({"tasks": [{"description": "   ", "prompt": "p"}]})

    assert "description 不能为空" in result


def test_rejects_non_object_item():
    assert "不是对象" in run({"tasks": ["字符串"]})


def test_validation_happens_before_any_subagent_runs():
    started = []

    run(
        {"tasks": [task("好的"), {"description": "坏的"}]},
        runner=lambda prompt, **kwargs: started.append(prompt) or "x",
    )

    assert started == []


# ---------- 并行调度与汇总 ----------


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

    assert seen == ["统计 src 下各文件行数", "列出所有测试文件"]
    assert result.startswith("已并行运行 2 个 subagent：")
    assert "=== 1/2 · 统计行数 ===" in result
    assert "=== 2/2 · 检查测试 ===" in result
    assert "摘要：统计 src 下各文件行数" in result


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
        if prompt == "炸":
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
    """两个任务都要在栅栏处会合：串行执行会撞上栅栏超时并失败。"""
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
    """免审批开关从 RunState 读，显式传给子运行——不是隐式的全局状态。"""
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


def test_run_subagent_initialises_messages_with_the_prompt(monkeypatch):
    from avid.agent import loop as agent_module

    seen = {}

    def fake_loop(messages, **kwargs):
        seen["messages"] = messages
        seen.update(kwargs)
        return "摘要"

    monkeypatch.setattr(agent_module, "agent_loop", fake_loop)

    assert run_subagent("任务原文", config=CONFIG) == "摘要"
    assert seen["messages"] == [{"role": "user", "content": "任务原文"}]
    assert seen["system"] == SUB_SYSTEM
    assert seen["tools"] is SUB_TOOLS
    assert seen["registry"] is SUB_HANDLERS
    # 子 agent 不传任何轮数预算：内核没有这个概念（旧代码在这里写死过 30 轮）。
    assert "max_rounds" not in seen


def test_run_subagent_returns_no_summary_for_empty_text(monkeypatch):
    from avid.agent import loop as agent_module

    monkeypatch.setattr(agent_module, "agent_loop", lambda *a, **k: "   ")

    assert run_subagent("随便", config=CONFIG) == "(no summary)"


# ---------- 取消传导、事件嵌套与 usage 并账（阶段 30c） ----------


def test_check_cancelled_consults_the_external_probe():
    from avid.agent.loop import RunCancelled

    state = RunState(cancel_probe=lambda: "外部要求停止")

    with pytest.raises(RunCancelled, match="外部要求停止"):
        state.check_cancelled()


def test_probe_none_means_no_external_source():
    RunState().check_cancelled()  # 不抛


def test_adopt_child_usage_sums_tokens_and_counts_calls():
    from avid.providers.usage import Usage

    parent = RunState()
    child = RunState()
    child.record_usage(Usage(5, 2, 7))
    child.record_usage(Usage(1, 1, 2))

    parent.adopt_child_usage(child)

    assert parent.child_tokens == 9
    assert parent.child_calls == 1
    assert parent.tokens == 9  # 运行总开销 = 主循环 + 子 agent


def test_usage_report_carries_subagent_totals():
    from avid.providers.usage import Usage

    parent = RunState()
    assert parent.usage_report()["subagent"] == {"calls": 0, "tokens": 0}

    child = RunState()
    child.record_usage(Usage(5, 2, 7))
    parent.adopt_child_usage(child)

    assert parent.usage_report()["subagent"] == {"calls": 1, "tokens": 7}


def test_run_subagent_wires_probe_observer_and_state(monkeypatch):
    """run_subagent 自建子 RunState：probe/observer 落在它身上，引用交给 on_state。"""
    from avid.agent import loop as agent_module
    from avid.agent.loop import RunCancelled

    seen = {}

    def fake_loop(messages, *, state=None, **kwargs):
        seen["state"] = state
        state.check_cancelled()  # probe 在这里生效
        return "不该到这里"

    monkeypatch.setattr(agent_module, "agent_loop", fake_loop)

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
    """子事件带 subagent 标记进父事件流；结束后子 token 并进父台账。"""
    import avid.agent.events as runtime_events
    from avid.agent import loop as agent_module
    from avid.providers.usage import Usage

    def fake_loop(messages, *, state=None, **kwargs):
        state.record_usage(Usage(5, 2, 7))
        state.emit(runtime_events.RUN_STATUS, round=1, tokens=7, activity="model")
        return "ok"

    monkeypatch.setattr(agent_module, "agent_loop", fake_loop)

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
    """父取消后 collect 提前收敛返回，不再等慢子任务自然结束。"""
    from avid.agent import loop as agent_module

    started = threading.Event()

    def fake_loop(messages, *, state=None, **kwargs):
        started.set()
        time.sleep(5)  # 比测试耐心长得多
        return "太慢"

    monkeypatch.setattr(agent_module, "agent_loop", fake_loop)

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
    """墙钟到点：超时文案照回，同时子任务在下个检查点被 probe 停掉（不再是孤儿）。"""
    from avid.agent import loop as agent_module
    from avid.agent.loop import RunCancelled

    raised = []

    def fake_loop(messages, *, state=None, **kwargs):
        time.sleep(0.3)  # 超过 timeout
        try:
            state.check_cancelled()
        except RunCancelled as exc:
            raised.append(str(exc))
            raise
        return "不该到这里"

    monkeypatch.setattr(agent_module, "agent_loop", fake_loop)

    result = run({"tasks": [task("慢", "p")]}, timeout=0.1)
    assert "timed out" in result

    for _ in range(50):  # 等孤儿线程走到检查点
        if raised:
            break
        time.sleep(0.05)
    assert raised and "墙钟" in raised[0]
