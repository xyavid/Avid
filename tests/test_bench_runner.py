"""AvidBench 编排层的零模型成本测试：用 `ScriptedChat` 走完整链路。

跑的是真工具（bash / read_file）、真判定器、真落盘，只有模型是脚本化的。
这样仪器本身的 bug 在花真模型的钱之前就会暴露。
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest
from support import ScriptedChat, make_turn, tool_call

from avid.ai.client import Config
from avid.runtime.state import RunState
from benchmarks.avidbench import load_cases, run_all
from benchmarks.avidbench.case import Limits
from benchmarks.avidbench.result import classify
from benchmarks.avidbench.runner import BenchmarkError, run_case
from benchmarks.avidbench.telemetry import Telemetry
from benchmarks.avidbench.variants import (
    CORE_TOOLS,
    SYSTEM_PROMPT,
    VARIANTS,
    hooks_for,
    run_agent,
    schemas,
)
from benchmarks.avidbench.workspace import materialized

CONFIG = Config(api_key="test", base_url="http://example.invalid/v1", model="scripted")

ALL_VARIANTS = ("bare", "core", "full")


def b01_turns() -> list:
    """一次 bash 调用 + 一个正确答案。"""
    return [
        make_turn(tool_calls=[tool_call("bash", '{"command": "wc -l beta.txt"}')]),
        make_turn(text="beta.txt 137"),
    ]


def turns_for(case) -> list:
    if case.followup is not None:
        return [
            make_turn(text="raw=8842"),
            make_turn(text="code=VER-8842"),
            make_turn(text="code=VER-8842"),
        ]
    return b01_turns()


@pytest.mark.parametrize("variant", ALL_VARIANTS)
def test_every_variant_solves_b01_and_writes_records(variant: str, tmp_path: Path):
    case = load_cases(ids=["b01_largest_file"])[0]
    results = run_case(
        case, variant, config=CONFIG, chat=ScriptedChat(*turns_for(case)), out_dir=tmp_path
    )
    assert len(results) == 1
    result = results[0]
    assert result.status == "resolved"
    assert result.resolved and all(item["passed"] for item in result.graders)
    assert result.metrics["rounds"] == 2
    assert result.metrics["tool_calls"] == 1
    assert result.metrics["tokens"] == 6  # 脚本模型每轮 3
    assert result.workspace_pristine
    directory = tmp_path / case.id / variant
    assert (directory / "result.json").is_file()
    assert (directory / "trajectory.jsonl").is_file()
    assert (directory / "answer.txt").read_text(encoding="utf-8").strip() == "beta.txt 137"


def test_wrong_answer_is_unresolved_but_still_recorded(tmp_path: Path):
    case = load_cases(ids=["b01_largest_file"])[0]
    turns = [
        make_turn(tool_calls=[tool_call("bash", '{"command": "wc -l *"}')]),
        make_turn(text="beta.txt 138"),
    ]
    result = run_case(case, "core", config=CONFIG, chat=ScriptedChat(*turns), out_dir=tmp_path)[0]
    assert result.status == "unresolved" and not result.resolved
    assert any(not item["passed"] for item in result.graders)
    assert classify(result) == "能力"


@pytest.mark.parametrize("variant", ALL_VARIANTS)
def test_telemetry_matches_run_state_in_both_loops(variant: str):
    """指标从事件流派生，必须与 `RunState` 的计数逐项一致——不一致就是漏了事件。"""
    case = load_cases(ids=["b01_largest_file"])[0]
    with materialized(case) as root:
        telemetry = Telemetry()
        state = RunState.for_run(
            auto_approve=True,
            observer=telemetry.on_event,
            workspace_root=str(root),
            hooks=hooks_for(VARIANTS[variant]),
        )
        answer = run_agent(
            VARIANTS[variant],
            messages=[{"role": "user", "content": case.prompt}],
            config=CONFIG,
            chat=ScriptedChat(*turns_for(case)),
            state=state,
            on_message=telemetry.on_message,
        )
    metrics = telemetry.metrics()
    assert answer == "beta.txt 137"
    assert metrics["rounds"] == state.round == 2
    assert metrics["tokens"] == state.tokens == 6
    assert metrics["tool_calls"] == state.tool_calls == 1
    assert metrics["denials"] == state.denials
    assert metrics["compactions"] == state.compactions
    assert [item["kind"] for item in telemetry.trajectory].count("tool_call") == 1


def test_tool_sets_are_the_ablation():
    bare = [str(item["function"]["name"]) for item in schemas(VARIANTS["bare"])]
    core = [str(item["function"]["name"]) for item in schemas(VARIANTS["core"])]
    full = {str(item["function"]["name"]) for item in schemas(VARIANTS["full"])}
    assert set(bare) == set(CORE_TOOLS)
    assert core == bare, "core 与 bare 必须同工具集，否则量到的是工具差异"
    assert set(core) < full
    assert {"subagent", "todo_write", "load_skill"} <= full


def test_bare_runs_without_hooks_and_core_uses_defaults():
    assert hooks_for(VARIANTS["bare"]).registered("PostToolUse") == []
    assert hooks_for(VARIANTS["core"]) is None
    assert hooks_for(VARIANTS["full"]) is None


def test_system_prompt_is_shared_by_all_variants():
    case = load_cases(ids=["b01_largest_file"])[0]
    with materialized(case) as root:
        prompts = set()
        for variant in ALL_VARIANTS:
            state = RunState.for_run(
                workspace_root=str(root), hooks=hooks_for(VARIANTS[variant])
            )
            prompts.add(state.system_prompt(SYSTEM_PROMPT))
    assert len(prompts) == 1, "三个变体的系统提示词必须同源"


def test_followup_resume_carries_history_and_fresh_does_not(tmp_path: Path):
    case = load_cases(ids=["m01_fact_carryover"])[0]
    chat = ScriptedChat(*turns_for(case))
    results = run_case(case, "full", config=CONFIG, chat=chat, out_dir=tmp_path)
    assert [item.condition for item in results] == ["phase1", "resume", "fresh"]
    assert results[0].status == "unscored", "第一轮不计分"
    assert results[1].resolved and results[2].resolved
    messages = [request["messages"] for request in chat.requests]
    assert len(messages[0]) == 1
    assert len(messages[1]) == 3, "resume：第一轮的两条消息 + 第二轮 prompt"
    assert len(messages[2]) == 1, "fresh：不许把第一轮的历史偷偷带过来"


def test_followup_refuses_variants_without_a_session_layer():
    case = load_cases(ids=["m01_fact_carryover"])[0]
    for variant in ("bare", "core"):
        with pytest.raises(BenchmarkError):
            run_case(case, variant, config=CONFIG, chat=ScriptedChat(make_turn(text="x")))


def test_hard_timeout_cancels_through_the_existing_checkpoint():
    case = load_cases(ids=["b01_largest_file"])[0]
    tight = dataclasses.replace(
        case, limits=Limits(timeout_seconds=0.05)
    )

    class SlowChat:
        def __call__(self, config, messages, **kwargs):
            time.sleep(0.3)
            return make_turn(tool_calls=[tool_call("bash", '{"command": "true"}')])

    result = run_case(tight, "core", config=CONFIG, chat=SlowChat())[0]
    assert result.status == "timeout" and not result.resolved
    assert classify(result) == "预算"
    assert any(item["passed"] for item in result.graders)  # 判定仍照跑，只是不算成功


def test_internal_errors_become_an_error_status():
    case = load_cases(ids=["b01_largest_file"])[0]

    class BrokenChat:
        def __call__(self, config, messages, **kwargs):
            raise ValueError("boom")

    result = run_case(case, "core", config=CONFIG, chat=BrokenChat())[0]
    assert result.status == "error"
    assert result.error and "boom" in result.error
    assert classify(result) == "基础设施"


def test_run_all_skips_session_case_for_bare_and_core(tmp_path: Path):
    cases = load_cases(ids=["b01_largest_file", "m01_fact_carryover"])
    run_set = run_all(
        cases,
        ALL_VARIANTS,
        config=CONFIG,
        chat_factory=lambda case, variant: ScriptedChat(*turns_for(case)),
        out_dir=tmp_path,
    )
    arms = {item.arm for item in run_set.results}
    assert arms == {"bare", "core", "full", "full--phase1", "full--resume", "full--fresh"}
    assert len(run_set.results) == 6  # b01×3 + m01（phase1 + 两种条件）
    summary = run_set.summary()
    assert "AvidBench v0.1" in summary
    assert (tmp_path / "summary.txt").is_file() and (tmp_path / "results.json").is_file()
    # 未计分的臂（跨会话第一轮）不进 resolved 分母，但也不能显示成 0/0
    assert "未计分" in summary and "0/0" not in summary


def test_classify_covers_the_five_categories():
    from benchmarks.avidbench.result import RunResult

    def status(name: str) -> str:
        return classify(RunResult(case_id="c", category="basic", variant="bare", status=name))

    assert status("resolved") == ""
    assert status("unresolved") == "能力"
    assert status("timeout") == "预算"
    assert status("llm_error") == "模型"
    assert status("error") == "基础设施"
    assert status("unscored") == ""


def test_injected_context_chars_is_recorded_only_where_it_applies(tmp_path: Path):
    """注入值必须进 `overrides`，否则两组对照的数字无法解释。

    `bare` 没有压缩机制：给它一个阈值等于无声无效，所以既不传也不记——记了就会凭空
    多出一个不存在的差异来源。
    """
    case = load_cases(ids=["b01_largest_file"])[0]
    for variant, expected in (
        ("bare", {}),
        ("core", {"context_chars": 30_000}),
        ("full", {"context_chars": 30_000}),
    ):
        result = run_case(
            case,
            variant,
            config=CONFIG,
            chat=ScriptedChat(*b01_turns()),
            out_dir=tmp_path,
            context_chars=30_000,
        )[0]
        assert result.overrides == expected, variant
        assert result.status == "resolved", variant


def test_summary_shows_the_injected_value(tmp_path: Path):
    case = load_cases(ids=["b01_largest_file"])[0]
    run_set = run_all(
        [case],
        ("core",),
        config=CONFIG,
        chat_factory=lambda case, variant: ScriptedChat(*b01_turns()),
        out_dir=tmp_path,
        context_chars=30_000,
    )
    assert "注入：context_chars=30000" in run_set.summary()
    assert run_set.overrides == {"context_chars": 30_000}
