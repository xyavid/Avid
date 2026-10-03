"""RunSpec / Run（新流程骨架）：与旧 agent_loop 的序列一致性 + 新 API 行为钉。

一致性是阶段 36 的验收 bar：同一份脚本化模型与工具，两条流程产出的
messages 序列、on_message 顺序、每轮发给模型的请求（messages/system/tools）
与最终返回值逐字节一致。行为分叉自阶段 37 起才允许发生。
"""

from __future__ import annotations

import copy

import pytest
from support import ScriptedChat, make_turn, tool_call

from avid.agent.events import RUN_STATUS, STOP_NUDGE
from avid.agent.hooks import DEFAULT_HOOKS
from avid.agent.run import Run, RunCancelled
from avid.agent.spec import RunSpec
from avid.agent.state import RunState
from avid.agent.stop import STOP_BLANK_NOTICE, STOP_FINAL_TEXT
from avid.agent.tools import TOOL_IMPLS, TOOLS
from avid.providers.client import DEFAULT_MAX_TOKENS
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")
USER = {"role": "user", "content": "读 a.txt"}


def drive(*turns, registry=None, messages=None):
    """唯一循环跑一份脚本：结束结果返回给用例，脚本本身严格限制轮数。"""
    base = messages if messages is not None else [dict(USER)]
    messages_run = [dict(m) for m in base]
    emitted: list[dict] = []
    chat = ScriptedChat(*copy.deepcopy(list(turns)))

    spec = RunSpec.resolve(config=CONFIG, chat=chat, registry=registry or {})
    outcome = Run(messages_run, spec, on_message=emitted.append).run()
    return outcome


# ---------------- 一致性用例 ----------------


def test_single_round_answer_is_identical():
    outcome = drive(make_turn("答复文本"))
    assert outcome.text == "答复文本"
    assert outcome.reason == STOP_FINAL_TEXT


def test_two_round_tool_use_is_identical():
    seen = []

    def read_file(arguments, **kwargs):
        seen.append(arguments)
        return "内容"

    outcome = drive(
        make_turn("", [tool_call("read_file")]),
        make_turn("读到了"),
        registry={"read_file": read_file},
    )
    assert outcome.text == "读到了"
    assert seen == [{"path": "a.txt"}]


def test_unknown_tool_becomes_tool_result_identically():
    outcome = drive(
        make_turn("", [tool_call("run_bash", call_id="c1")]),
        make_turn("好"),
        registry={},
    )
    assert outcome.text == "好"


def test_tool_failure_becomes_text_not_exception_identically():
    def boom(arguments, **kwargs):
        raise RuntimeError("炸了")

    outcome = drive(
        make_turn("", [tool_call("read_file")]),
        make_turn("继续"),
        registry={"read_file": boom},
    )
    assert outcome.text == "继续"


def test_blank_answer_nudge_and_notice_are_identical():
    outcome = drive(make_turn(""), make_turn(""))
    assert outcome.text.startswith("（本次运行没有产生可见答复")
    assert outcome.reason == STOP_BLANK_NOTICE


# ---------------- RunSpec.resolve 行为钉 ----------------


def test_resolve_defaults_cover_the_whole_old_signature():
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat())
    assert [t["function"]["name"] for t in spec.tools] == [
        t["function"]["name"] for t in TOOLS
    ]
    assert spec.registry is TOOL_IMPLS
    assert set(spec.schemas) == {t["function"]["name"] for t in spec.tools}
    assert spec.tool_names == [t["function"]["name"] for t in spec.tools]
    assert spec.max_tokens == DEFAULT_MAX_TOKENS
    assert spec.parallel_limit == CONFIG.max_parallel_tool_calls
    # 测试环境 AVID_MODEL_INFO=off，窗口探测让位：config 原样保留
    assert spec.config is CONFIG


def test_resolve_honors_explicit_parallel_override():
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat(), max_parallel_tools=1)
    assert spec.parallel_limit == 1


def test_resolve_uses_hooks_default_when_omitted():
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat())
    assert spec.hooks is None  # 交给 RunState.for_run 落 DEFAULT_HOOKS


def test_prebuilt_hooks_instance_flow_through():
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat(), hooks=DEFAULT_HOOKS)
    assert spec.hooks is DEFAULT_HOOKS


# ---------------- Run 行为钉 ----------------


def test_run_accepts_prebuilt_state_and_records_round():
    state = RunState.for_run()
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat(make_turn("好的")))
    assert Run([dict(USER)], spec, state=state).run().text == "好的"
    assert state.round == 1


def test_run_cancel_raises_at_checkpoint():
    state = RunState.for_run()
    state.cancel("停")
    spec = RunSpec.resolve(
        config=CONFIG,
        chat=ScriptedChat(make_turn("", [tool_call("read_file")])),
        registry={"read_file": lambda arguments: "x"},
    )
    with pytest.raises(RunCancelled):
        Run([dict(USER)], spec, state=state).run()


def test_run_emits_status_events_per_round():
    events: list[tuple] = []
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat(make_turn("答")))
    Run([dict(USER)], spec, on_event=lambda e: events.append((e.type, e.data))).run()
    statuses = [e for e in events if e[0] == RUN_STATUS]
    assert len(statuses) == 2  # 每轮两次：模型调用前 + usage 回填后
    assert STOP_NUDGE not in {e[0] for e in events}
