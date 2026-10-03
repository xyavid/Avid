"""压缩阈值随窗口派生的测试（解决"字符阈值与 context_window 解耦"）。

分两层测，因为它们的所有者不同：

* `compact.derived_context_chars` 是**纯函数**：只看窗口、上一轮真实 `prompt_tokens`
  与当次三块字符数，不碰运行状态、不碰模型；
* `context.effective_budget` 是**编排层的取值点**：决定这次到底用哪个阈值，以及
  派生"什么时候不生效"（无关掉、缺窗口、缺读数）。

两条口径都是"实测反推"：`chars/token` 来自**这次运行自己的真实读数**，不是查表得来的
语言系数——所以中英混排时它自动跟着走。
"""

from __future__ import annotations

import pytest

from avid.agent import compaction as compact
from avid.agent.compaction import derived_context_chars
from avid.agent.context import ContextBudget, ContextManager, effective_budget
from avid.agent.state import RunState
from avid.providers.client import Usage
from avid.providers.config import Config
from avid.providers.transcript import Transcript

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")

#: 三块文本的字符数（系统提示 / 工具定义 / 对话消息）。故意让两块固定文本 ~24k 字符：
#: 默认常量 400_000 正是"128k 窗口 × 0.8 × 4 字符/token − 24k"这一种情形。
PARTS_EN = (20_000, 4_000, 300_000)


def user(text: str = "hi") -> dict[str, str]:
    return {"role": "user", "content": text}


def not_called(*args, **kwargs):
    raise AssertionError("这个用例不该调用模型")


# ---------------- 纯函数：窗口 → 字符阈值 ----------------


def test_english_128k_reproduces_todays_default_constant():
    """校准断言：128k 窗口 + 英文（≈4 字符/token）算出来就是今天的 400k。

    默认常量不是错的，它只是**被写死**了——它本来就等于这一种情形。改造之后它退居
    "窗口或读数拿不到时的回落值"。
    """
    limit, cpt = derived_context_chars(
        window=128_000, prompt_tokens=81_000, chars=PARTS_EN
    )

    assert cpt == pytest.approx(4.0)
    assert limit == 385_600
    # 与写死的 400_000 同一个量级：改动是"换算法"，不是"换了一档阈值"。
    assert 350_000 <= limit <= 450_000


def test_cjk_gets_a_much_lower_threshold_than_english():
    """中文 1 字符 ≈ 1 token：同一个窗口，阈值必须小得多——固定 400k 的病根。"""
    chars = (20_000, 4_000, 376_000)  # 合计 40 万字符

    english, cpt_en = derived_context_chars(
        window=128_000, prompt_tokens=100_000, chars=chars
    )
    chinese, cpt_zh = derived_context_chars(
        window=128_000, prompt_tokens=400_000, chars=chars
    )

    assert cpt_en == pytest.approx(4.0)
    assert cpt_zh == pytest.approx(1.0)
    assert chinese < english / 3


def test_small_window_derives_a_threshold_inside_the_window():
    """32k 窗口 + 中文：把阈值折回 token 必须仍在窗口内。

    这一条是"压缩发生在发请求之前"的关键——阈值不能等到 provider 报错才开始起作用。
    """
    limit, cpt = derived_context_chars(
        window=32_768, prompt_tokens=104_000, chars=(20_000, 4_000, 80_000)
    )

    assert limit == 2_214
    # 同一个换算率折回 token：阈值 + 两块固定文本 ≤ 窗口 × ratio
    assert (limit + 20_000 + 4_000) / cpt <= 32_768 * compact.WINDOW_TRIGGER_RATIO


def test_ratio_is_a_parameter():
    """比例可注入：0.4 的阈值正好是 0.8 的一半（评测要能单变量对照）。"""
    high, _ = derived_context_chars(
        window=100_000, prompt_tokens=100_000, chars=(0, 0, 100_000)
    )
    low, _ = derived_context_chars(
        window=100_000, prompt_tokens=100_000, chars=(0, 0, 100_000), ratio=0.4
    )

    assert high == 80_000
    assert low == 40_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window": None, "prompt_tokens": 1_000, "chars": (1, 1, 100)},
        {"window": 0, "prompt_tokens": 1_000, "chars": (1, 1, 100)},
        {"window": 128_000, "prompt_tokens": None, "chars": (1, 1, 100)},
        {"window": 128_000, "prompt_tokens": 0, "chars": (1, 1, 100)},
        {"window": 128_000, "prompt_tokens": 1_000, "chars": None},
        {"window": 128_000, "prompt_tokens": 1_000, "chars": (0, 0, 0)},
    ],
)
def test_derivation_needs_all_three_premises(kwargs):
    """缺任一项就不猜：宁可回落到常量，也不拿半份读数算一个假的阈值。"""
    assert derived_context_chars(**kwargs) is None


def test_chars_per_token_is_clamped_to_a_plausible_band():
    """读数离谱时不跟着跑：下界挡"过度压缩"，上界挡"永远不压"。"""
    low, cpt_low = derived_context_chars(
        window=128_000, prompt_tokens=100_000, chars=(0, 0, 1_000)
    )
    high, cpt_high = derived_context_chars(
        window=128_000, prompt_tokens=1_000, chars=(0, 0, 1_000_000)
    )

    assert cpt_low == compact.MIN_CHARS_PER_TOKEN
    assert low == int(128_000 * compact.WINDOW_TRIGGER_RATIO * 0.5)
    assert cpt_high == compact.MAX_CHARS_PER_TOKEN
    assert high == int(128_000 * compact.WINDOW_TRIGGER_RATIO * 6.0)


def test_limit_never_drops_below_one():
    """两块固定文本自己就把预算吃光时钳到 1，而不是 0 或负数。"""
    limit, _ = derived_context_chars(
        window=8_192, prompt_tokens=10_000, chars=(9_000, 1_000, 10)
    )

    assert limit == 1


# ---------------- 编排层：这次到底用哪个阈值 ----------------


def state_with_readings(window: int = 32_768) -> RunState:
    """一份"跑过一轮"的运行状态：真实读数与三块字符数是同一对。"""
    state = RunState(context_window=window)
    state.prompt_parts = (20_000, 4_000, 80_000)
    state.last_usage = Usage(104_000, 1, 104_001)
    return state


def test_effective_budget_derives_from_this_runs_own_reading():
    limits, note = effective_budget(ContextBudget(), state_with_readings())

    assert limits.context_chars == 2_214
    assert note is not None and "窗口" in note and "字符/token" in note
    # 派生值由 ②③④ 共用（② 的 snip 触发看字符预算，诊断 C1）；① 的工具结果预算不动。
    assert limits.tool_result_chars == compact.TOOL_RESULT_CHAR_BUDGET


@pytest.mark.parametrize(
    "state",
    [
        RunState(),  # 不认识这个模型的窗口
        RunState(context_window=32_768),  # 有窗口但这一轮还没有真实读数
    ],
)
def test_effective_budget_falls_back_to_the_constant(state):
    limits, note = effective_budget(ContextBudget(), state)

    assert limits.context_chars == compact.CONTEXT_CHAR_LIMIT
    assert note is None


def test_explicit_budget_is_not_overwritten():
    """显式注入阈值做单变量对照时，派生必须让路（否则注入的那个数被静默盖掉）。"""
    limits, note = effective_budget(
        ContextBudget(context_chars=12_345, from_window=False),
        state_with_readings(),
    )

    assert limits.context_chars == 12_345
    assert note is None


def test_derived_limit_reaches_the_third_and_fourth_steps(monkeypatch):
    """编排真的把派生值交给 ③；③ 压下来之后 ④ 不该再花一次调用。"""
    state = state_with_readings()
    seen: list[int] = []

    def fake_micro(transcript, **kwargs):
        seen.append(kwargs["limit"])
        transcript.replace_all([user("短")])
        return compact.CompactReport("micro_compact", "落盘若干项", 5_000, 2)

    monkeypatch.setattr(compact, "tool_result_budget", lambda t, **k: None)
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)
    monkeypatch.setattr(compact, "micro_compact", fake_micro)
    monkeypatch.setattr(
        compact,
        "compact_history",
        lambda t, **k: pytest.fail("③ 已经把上下文压下来了，不该再摘要"),
    )

    result = ContextManager(
        transcript=Transcript([user("x" * 5_000)]),
        state=state,
        config=CONFIG,
        summarize=not_called,
    ).compose()

    assert seen == [2_214]
    assert result.changed
    # 派生理由要出现在用户可见的 detail 里：界面回答"这次压缩为什么发生"。
    assert "阈值随窗口派生" in result.reports[0].detail


def test_default_path_detail_is_unchanged(monkeypatch):
    """回落路径（没有窗口/读数）的 detail 逐字不变——默认路径行为不许漂。"""
    state = RunState()
    original = "落盘 1 项较早的工具结果（保留最近 3 条）"

    def fake_micro(transcript, **kwargs):
        transcript.replace_all([user("短")])
        return compact.CompactReport("micro_compact", original, 5_000, 2)

    monkeypatch.setattr(compact, "tool_result_budget", lambda t, **k: None)
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)
    monkeypatch.setattr(compact, "micro_compact", fake_micro)
    monkeypatch.setattr(compact, "compact_history", lambda t, **k: pytest.fail("不该摘要"))

    result = ContextManager(
        transcript=Transcript([user("x" * 5_000)]),
        state=state,
        config=CONFIG,
        summarize=not_called,
        budget=ContextBudget(context_chars=100, from_window=False),
    ).compose()

    assert result.reports[0].detail == original
