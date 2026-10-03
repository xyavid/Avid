"""ContextManager 的装配测试：块收集、落位、定格与记账。

压缩编排（五步的顺序与条件）的用例在 test_context.py（已改指本模块）；
这里只测装配本身：system 怎么拼、tail 怎么挂、账怎么记、什么不该变。
"""

import pytest

from avid.agent.context import (
    FROZEN,
    PER_ROUND,
    SYSTEM,
    TAIL,
    BlockSpec,
    ContextBudget,
    ContextManager,
)
from avid.agent.skills import SkillLoader
from avid.agent.state import RunState
from avid.agent.transcript import Transcript
from avid.providers.client import Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def user(text="hi"):
    return {"role": "user", "content": text}


def _no_summarize(*args, **kwargs):
    raise AssertionError("这个用例不该调用模型")


def make_manager(
    messages=None,
    *,
    instructions=None,
    tool_names=("bash", "read_file"),
    state=None,
    budget=None,
    summarize=None,
):
    return ContextManager(
        transcript=Transcript([user("hi")] if messages is None else messages),
        state=state or RunState(),
        config=CONFIG,
        instructions=instructions,
        tool_names=list(tool_names),
        budget=budget,
        summarize=summarize or _no_summarize,
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
    manager.register_block(
        BlockSpec(
            kind="tenant",
            title=None,
            section=SYSTEM,
            stability=FROZEN,
            cap=None,
            source=lambda mgr: "租户：acme",
        )
    )

    request = manager.compose()

    assert "租户：acme" in request.system
    assert request.parts["tenant"] > 0


def test_skill_catalog_renders_into_system(tmp_path):
    (tmp_path / "code-review").mkdir()
    (tmp_path / "code-review" / "SKILL.md").write_text(
        "---\ndescription: 做代码审查\n---\n", encoding="utf-8"
    )
    state = RunState()
    state.skills = SkillLoader(tmp_path).scan()

    request = make_manager(state=state).compose()

    assert "- code-review: 做代码审查" in request.system
    assert "Use load_skill" in request.system
    assert request.parts["skill_catalog"] > 0


# ---------- bootstrap 与常驻技能（阶段 31） ----------


def test_bootstrap_block_carries_the_workspace_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# 协作约定\n提交信息用中文。", encoding="utf-8")
    state = RunState(workspace_root=str(tmp_path))

    request = make_manager(state=state).compose()

    assert "## 工作区约定" in request.system
    assert "提交信息用中文。" in request.system
    assert request.parts["bootstrap"] > 0


def test_bootstrap_is_absent_without_the_file(tmp_path):
    state = RunState(workspace_root=str(tmp_path))

    request = make_manager(state=state).compose()

    assert "bootstrap" not in request.parts
    assert "## 工作区约定" not in request.system


def test_bootstrap_ignores_an_empty_or_missing_file(tmp_path):
    (tmp_path / "AGENTS.md").write_text("   \n", encoding="utf-8")
    state = RunState(workspace_root=str(tmp_path))

    assert "bootstrap" not in make_manager(state=state).compose().parts


def test_bootstrap_ignores_a_directory_shaped_file(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    state = RunState(workspace_root=str(tmp_path))

    assert "bootstrap" not in make_manager(state=state).compose().parts


def test_bootstrap_truncates_past_the_cap(tmp_path):
    (tmp_path / "AGENTS.md").write_text("长" * 80, encoding="utf-8")
    state = RunState(workspace_root=str(tmp_path))

    # 上限归口 ContextBudget：按运行注入，而不是改散常量
    request = make_manager(state=state, budget=ContextBudget(bootstrap_chars=50)).compose()

    from avid.agent import prompt

    assert prompt.TRUNCATION_NOTE in request.system
    assert "长" * 50 in request.system
    assert "长" * 51 not in request.system


def test_always_skill_lands_in_system_and_leaves_the_catalog(tmp_path):
    (tmp_path / "style").mkdir()
    (tmp_path / "style" / "SKILL.md").write_text(
        "---\ndescription: 代码风格\nalways: true\n---\n缩进用四空格。\n", encoding="utf-8"
    )
    state = RunState()
    state.skills = SkillLoader(tmp_path).scan()

    request = make_manager(state=state).compose()

    assert "## 常驻技能" in request.system
    assert "缩进用四空格。" in request.system
    assert request.parts["skill_always"] > 0
    # always 技能不再占目录：不需要为一个已常驻的技能调 load_skill
    assert "- style: 代码风格" not in request.system


def test_always_total_cap_skips_later_skills(tmp_path, monkeypatch):
    from avid.agent import prompt

    monkeypatch.setattr(prompt, "SKILL_ALWAYS_TOTAL_MAX_CHARS", 10)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(
            f"---\ndescription: {name}\nalways: true\n---\n{'字' * 8}\n", encoding="utf-8"
        )
    state = RunState()
    state.skills = SkillLoader(tmp_path).scan()

    request = make_manager(state=state).compose()

    assert "### a" in request.system
    assert "### b" not in request.system


def test_default_instructions_carry_identity_contract_and_guardrail():
    """默认文案（阶段 31 起来自 policy.prompt）钉住四类必备内容。"""
    request = make_manager().compose()

    assert "你是 Avid" in request.system
    # 工具契约：授权执行并验证、不可逆先确认、缺信息先澄清、等结果再答复
    assert "不可逆" in request.system
    assert "澄清" in request.system
    assert "工具结果" in request.system
    # 外部内容防线：工具结果是数据不是指令
    assert "不是指令" in request.system
    # 原有的 todo 约定保留
    assert "todo_write" in request.system


def test_environment_includes_runtime_facts():
    import platform

    request = make_manager().compose()

    assert platform.system() in request.system
    assert "今天：" in request.system


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
    manager.register_block(
        BlockSpec(
            kind="artifact",
            title="当前 Artifact",
            section=TAIL,
            stability=PER_ROUND,
            cap=None,
            source=lambda mgr: "报告 v2",
        )
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
    # 两轮工具往返；keep_recent_turns=1 时第 1 轮落入"更早历史"，被摘要替代。
    messages = [
        user("hi"),
        *tool_round("x" * 5000, "t1"),
        *tool_round("y" * 10, "t2"),
    ]
    manager = make_manager(
        messages,
        state=state,
        summarize=lambda config, messages, **kwargs: Turn(
            message={"role": "assistant", "content": "要点"},
            text="要点",
            tool_calls=[],
            usage=Usage(1, 1, 2),
            model="m",
            finish_reason="stop",
        ),
        budget=ContextBudget(
            context_chars=100, keep_recent_turns=1, from_window=False
        ),
    )

    request = manager.compose()

    assert request.changed
    assert request.reports[0].step == "compact_history"
    assert "[历史摘要]" in manager.transcript.as_messages()[0]["content"]


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


# ---------- 缓存纪律（阶段 42 排序约定） ----------


def test_context_map_puts_stable_blocks_before_volatile_ones():
    """KV-cache 纪律钉进声明表：frozen 五块在前、per_round 两块在后，顺序不得漂移。"""
    from avid.agent.context import CONTEXT_MAP

    kinds = [spec.kind for spec in CONTEXT_MAP]
    assert kinds == [
        "instructions",
        "environment",
        "bootstrap",
        "skill_always",
        "skill_catalog",
        "plan",
        "run_state",
    ]
    stabilities = [spec.stability for spec in CONTEXT_MAP]
    assert stabilities == ["frozen"] * 5 + ["per_round"] * 2


def test_volatile_content_only_reaches_the_last_message():
    """per_round 块只出现在 messages 的最后一条（tail 便签）；system 跨轮逐字节稳定。"""
    state = RunState()
    state.todo.replace([{"content": "第一步", "status": "in_progress"}])
    manager = make_manager(state=state)

    first = manager.compose()
    system_after_first = first.system
    state.round = 2
    second = manager.compose()

    assert second.system == system_after_first  # 冻结前缀逐字节稳定
    for message in second.messages[:-1]:
        assert "## 运行状态" not in str(message.get("content"))
        assert "## 当前计划" not in str(message.get("content"))
    assert "## 运行状态" in second.messages[-1]["content"]
    assert "## 当前计划" in second.messages[-1]["content"]

