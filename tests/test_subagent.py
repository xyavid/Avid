import threading
import time

import pytest

from avid.config import Config
from avid.tools import SUB_HANDLERS, SUB_TOOLS, TOOLS
from avid.tools.subagent import (
    MAX_PARALLEL,
    SUBAGENT_MAX_TURNS,
    SUB_SYSTEM,
    run_subagent,
    subagent,
)

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    """subagent() 会读环境变量建配置；测试里给一份假的。"""
    monkeypatch.setenv("AVID_API_KEY", "test-key")
    monkeypatch.setenv("AVID_MODEL", "test-model")


def task(description="干点活", prompt="把这件事做完"):
    return {"description": description, "prompt": prompt}


# ---------- 工具集与递归防护 ----------


def test_sub_tools_exclude_subagent():
    names = {item["function"]["name"] for item in SUB_TOOLS}
    all_names = {item["function"]["name"] for item in TOOLS}

    assert "subagent" not in names
    assert names == all_names - {"subagent"}
    assert names == set(SUB_HANDLERS)


def test_max_turns_is_30():
    assert SUBAGENT_MAX_TURNS == 30


def test_sub_system_asks_for_a_self_contained_summary():
    assert "摘要" in SUB_SYSTEM
    assert "不要反问" in SUB_SYSTEM


# ---------- 参数校验 ----------


def test_rejects_empty_tasks():
    assert "非空数组" in subagent({"tasks": []})


def test_rejects_non_list():
    assert "非空数组" in subagent({"tasks": "不是数组"})


def test_rejects_too_many_tasks():
    tasks = [task(f"第{i}", "p") for i in range(MAX_PARALLEL + 1)]

    result = subagent({"tasks": tasks})

    assert f"最多派发 {MAX_PARALLEL} 个" in result
    assert f"收到 {MAX_PARALLEL + 1} 个" in result


def test_rejects_missing_prompt():
    result = subagent({"tasks": [{"description": "只有描述"}]})

    assert "prompt 不能为空" in result


def test_rejects_blank_description():
    result = subagent({"tasks": [{"description": "   ", "prompt": "p"}]})

    assert "description 不能为空" in result


def test_rejects_non_object_item():
    assert "不是对象" in subagent({"tasks": ["字符串"]})


def test_validation_happens_before_any_subagent_runs():
    started = []

    subagent(
        {"tasks": [task("好的"), {"description": "坏的"}]},
        runner=lambda prompt, **kwargs: started.append(prompt) or "x",
    )

    assert started == []


# ---------- 并行调度与汇总 ----------


def test_runs_every_task_and_labels_the_results():
    seen = []

    def runner(prompt, *, config, auto_approve):
        seen.append(prompt)
        return f"摘要：{prompt}"

    result = subagent(
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
    result = subagent(
        {"tasks": [task()]}, runner=lambda prompt, **kwargs: "ok"
    )

    assert result.startswith("已运行 1 个 subagent：")


def test_empty_summary_becomes_no_summary():
    result = subagent(
        {"tasks": [task()]}, runner=lambda prompt, **kwargs: "   "
    )

    assert "(no summary)" in result


def test_one_failure_does_not_lose_the_others():
    def runner(prompt, **kwargs):
        if prompt == "炸":
            raise RuntimeError("内部错误")
        return "好"

    result = subagent(
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

    result = subagent({"tasks": [task("慢", "p")]}, runner=runner, timeout=0.05)

    assert "Subagent timed out after" in result


def test_tasks_actually_run_in_parallel():
    """两个任务都要在栅栏处会合：串行执行会撞上栅栏超时并失败。"""
    barrier = threading.Barrier(2, timeout=2)

    def runner(prompt, **kwargs):
        barrier.wait()
        return f"完成 {prompt}"

    result = subagent(
        {"tasks": [task("a", "a"), task("b", "b")]},
        runner=runner,
    )

    assert "完成 a" in result
    assert "完成 b" in result
    assert "failed" not in result


def test_auto_approve_is_read_in_the_calling_thread(monkeypatch):
    from avid.permission import bind_auto_approve

    seen = []

    def runner(prompt, *, auto_approve, config):
        seen.append(auto_approve)
        return "ok"

    with bind_auto_approve(True):
        subagent({"tasks": [task()]}, runner=runner)

    assert seen == [True]


def test_auto_approve_defaults_to_false(monkeypatch):
    seen = []

    def runner(prompt, *, auto_approve, config):
        seen.append(auto_approve)
        return "ok"

    subagent({"tasks": [task()]}, runner=runner)

    assert seen == [False]


# ---------- run_subagent ----------


def test_run_subagent_initialises_messages_with_the_prompt(monkeypatch):
    from avid import agent as agent_module

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
    assert seen["max_rounds"] == SUBAGENT_MAX_TURNS


def test_run_subagent_maps_turn_limit_to_the_required_message(monkeypatch):
    from avid import agent as agent_module

    def fake_loop(*args, **kwargs):
        raise agent_module.RoundLimitExceeded("超了")

    monkeypatch.setattr(agent_module, "agent_loop", fake_loop)

    assert (
        run_subagent("随便", config=CONFIG)
        == "Subagent stopped after 30 turns without a final answer."
    )


def test_run_subagent_returns_no_summary_for_empty_text(monkeypatch):
    from avid import agent as agent_module

    monkeypatch.setattr(agent_module, "agent_loop", lambda *a, **k: "   ")

    assert run_subagent("随便", config=CONFIG) == "(no summary)"
