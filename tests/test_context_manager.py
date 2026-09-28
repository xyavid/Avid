"""ContextManager 的装配测试：块收集、落位、定格与记账。

压缩编排（五步的顺序与条件）的用例在 test_context.py（已改指本模块）；
这里只测装配本身：system 怎么拼、tail 怎么挂、账怎么记、什么不该变。
"""

import pytest

from avid.ai.config import Config
from avid.ai.transcript import Transcript
from avid.runtime import context_manager as cm
from avid.runtime.context_manager import Block, ContextBudget, ContextManager
from avid.runtime.state import RunState

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def user(text="hi"):
    return {"role": "user", "content": text}


def make_manager(
    messages=None,
    *,
    instructions=None,
    tool_names=("bash", "read_file"),
    state=None,
    budget=None,
):
    return ContextManager(
        transcript=Transcript([user("hi")] if messages is None else messages),
        state=state or RunState(),
        config=CONFIG,
        instructions=instructions,
        tool_names=list(tool_names),
        budget=budget,
        summarize=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("这个用例不该调用模型")
        ),
    )


# ---------- system 落位 ----------


def test_system_assembles_instructions_environment_and_skills():
    request = make_manager().compose()

    assert request.system.startswith("你是 Avid")
    assert "## 环境" in request.system
    assert "工作目录：" in request.system
    assert "可用工具：bash、read_file" in request.system
    assert "Act, don't explain." in request.system
    assert "## 可用技能" in request.system
    assert "（当前没有可用技能）" in request.system
    assert "Use load_skill" in request.system
    # 顺序：指令 → 环境 → 技能
    assert (
        request.system.index("你是 Avid")
        < request.system.index("## 环境")
        < request.system.index("## 可用技能")
    )


def test_instructions_override_takes_effect():
    request = make_manager(instructions="你是 subagent，只做一件事。").compose()

    assert request.system.startswith("你是 subagent，只做一件事。")
    # 模板部分照常追加
    assert "## 可用技能" in request.system


def test_injected_lands_last_in_system_and_later_composes_ignore_it():
    manager = make_manager()

    first = manager.compose(injected=["[注入] 自定义背景"])
    second = manager.compose(injected=["[注入] 第二次不该再进来"])

    assert first.system.endswith("[注入] 自定义背景")
    assert second.system == first.system


def test_system_is_frozen_within_a_run():
    manager = make_manager()
    first = manager.compose()

    # 运行中途换工具清单，system 也不该跟着变（前缀缓存友好）
    manager.tool_names = ["bash"]
    second = manager.compose()

    assert first.system == second.system


def test_custom_system_section_source_joins_the_template():
    manager = make_manager()
    manager.register_source("tenant", lambda: Block("tenant", "租户：acme", cm.SYSTEM))

    request = manager.compose()

    assert "租户：acme" in request.system
    assert request.parts["tenant"] > 0


# ---------- tail 落位 ----------


def test_tail_carries_plan_and_run_state_and_never_touches_transcript():
    state = RunState()
    state.todo.replace([{"content": "第一步", "status": "in_progress"}])
    state.round = 3
    state.tool_calls = 5
    manager = make_manager(state=state)
    before = len(manager.transcript)

    request = manager.compose()

    tail = request.messages[-1]
    assert tail["role"] == "user"
    assert tail["content"].startswith("[上下文]")
    assert "## 当前计划" in tail["content"]
    assert "第一步" in tail["content"]
    assert "第 3 轮" in tail["content"]
    assert "5 次工具调用" in tail["content"]
    # tail 不落库：transcript 长度不变，里面的消息没有一条是 tail
    assert len(manager.transcript) == before
    assert all(msg is not tail for msg in manager.transcript.as_messages())
    assert all(msg["content"] != tail["content"] for msg in manager.transcript.as_messages())


def test_no_plan_block_when_todo_is_empty():
    request = make_manager().compose()

    tail = request.messages[-1]
    assert "## 当前计划" not in tail["content"]
    assert "## 运行状态" in tail["content"]


def test_plan_updates_every_round_while_system_stays_frozen():
    state = RunState()
    manager = make_manager(state=state)

    first = manager.compose()
    state.todo.replace([{"content": "新步骤", "status": "in_progress"}])
    second = manager.compose()

    assert second.system == first.system
    assert "## 当前计划" not in first.messages[-1]["content"]
    assert "新步骤" in second.messages[-1]["content"]


def test_custom_tail_source_lands_in_the_tail_message():
    manager = make_manager()
    manager.register_source(
        "artifact", lambda: Block("artifact", "## 当前 Artifact\n报告 v2", cm.TAIL)
    )

    request = manager.compose()

    assert "## 当前 Artifact" in request.messages[-1]["content"]
    assert request.parts["artifact"] > 0


# ---------- 记账 ----------


def test_parts_accounting_covers_every_rendered_block():
    state = RunState()
    state.todo.replace([{"content": "第一步", "status": "in_progress"}])
    manager = make_manager(state=state)

    request = manager.compose()

    parts = request.parts
    for kind in ("instructions", "environment", "skill_catalog", "plan", "run_state"):
        assert parts[kind] > 0, kind
    assert parts["history"] == manager.transcript.estimate_chars()
    assert parts["messages"] > parts["history"]  # tail 计入 messages 口径
    assert request.system_chars == len(request.system)
    assert request.messages_chars == parts["messages"]


# ---------- 与压缩编排的衔接 ----------


def tool_round(content: str, call_id: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


def test_compose_runs_compaction_and_reports_it(tmp_path):
    state = RunState(workspace_root=str(tmp_path))
    # ① 永远保留最近 keep_recent 条工具结果，所以至少要有一条"更早的"才会落盘。
    messages = [
        user("hi"),
        *tool_round("x" * 5000, "t1"),
        *tool_round("y" * 10, "t2"),
    ]
    manager = make_manager(
        messages,
        state=state,
        budget=ContextBudget(
            tool_result_chars=100, tool_result_keep_recent=1, from_window=False
        ),
    )

    request = manager.compose()

    assert request.changed
    assert request.reports[0].step == "tool_result_budget"
    assert "已落盘" in manager.transcript.text_at(2)


def test_render_recomputes_tail_without_recompacting():
    state = RunState()
    state.todo.replace([{"content": "第一步", "status": "in_progress"}])
    manager = make_manager(state=state)
    first = manager.compose()

    state.todo.replace([{"content": "第二步", "status": "completed"}])
    second = manager.render()

    assert second.system == first.system
    assert "第二步" in second.messages[-1]["content"]
    assert second.reports == []


def test_render_before_compose_is_rejected():
    manager = make_manager()

    with pytest.raises(RuntimeError):
        manager.render()
