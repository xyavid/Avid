"""补充输入的交付点（阶段 60）：只在轮次边界领取，落成一条普通用户消息。

先于实现编写（§6）。盯四件事：
① 只在 step 边界交付（工具结果之后、下一个模型请求之前，结构安全）；
② 「模型说完了」那一刻也先问一句「还有补充吗」——有就继续跑；
③ 没有通道时行为与从前逐字一致；
④ 取消优先于补充（检查点先跑，被取消的 run 不会先把新消息吃进来）。
"""

from __future__ import annotations

import copy

from support import ScriptedChat, make_turn, tool_call

from avid.agent.run import Run, RunCancelled
from avid.agent.spec import RunSpec
from avid.agent.state import RunState
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")
USER = {"role": "user", "content": "读 a.txt"}
STEER = {"role": "user", "content": "先别改代码，解释根因"}


def drive(*turns, registry=None, steers=None, messages=None):
    """跑一份脚本；steers 是「还没被领走的补充」批次列表，每次领取给一批。"""
    base = messages if messages is not None else [dict(USER)]
    messages_run = [dict(m) for m in base]
    emitted: list[dict] = []
    chat = ScriptedChat(*copy.deepcopy(list(turns)))
    batches = [list(batch) for batch in (steers or [])]

    def take() -> list[dict]:
        return batches.pop(0) if batches else []

    state = RunState.for_run(steers=take)
    spec = RunSpec.resolve(config=CONFIG, chat=chat, registry=registry or {})
    outcome = Run(messages_run, spec, state=state, on_message=emitted.append).run()
    return outcome, messages_run, emitted, chat.requests


def test_a_steer_is_delivered_at_the_next_step_boundary():
    """工具批跑着的时候投的补充：等这批落地，在下一个模型请求之前进去。"""
    outcome, messages, emitted, requests = drive(
        make_turn("", [tool_call("read_file")]),
        make_turn("好"),
        registry={"read_file": lambda arguments, **kwargs: "内容"},
        steers=[[], [dict(STEER)]],  # 第一轮边界还没有；读完文件后的边界才有
    )

    assert outcome.text == "好"
    # 结构安全：工具结果先落地，补充接在它后面，再是下一轮的模型请求
    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "user", "assistant"]
    assert messages[3] == STEER
    # 第二个请求里能看到它（真正进了模型上下文；末尾那条是每轮重建的 tail 便签）
    assert requests[1]["messages"][3] == STEER
    assert requests[1]["messages"][-1]["content"].startswith("[上下文]")
    # 落库出口（on_message）也看到它：这就是它变成会话条目的那一步
    assert STEER in emitted


def test_a_steer_already_waiting_is_delivered_before_the_first_call():
    """起 run 与投递撞在一起（客户端以为空闲）：它进第一批，模型第一次请求就看得到。"""
    _outcome, messages, _emitted, requests = drive(
        make_turn("好"),
        steers=[[dict(STEER)]],
    )

    assert [message["role"] for message in messages] == ["user", "user", "assistant"]
    assert requests[0]["messages"][0] == USER
    assert requests[0]["messages"][1] == STEER


def test_a_steer_keeps_a_finishing_run_alive():
    """模型说「我说完了」，但边界上还有补充：先别收尾。"""
    outcome, messages, _emitted, requests = drive(
        make_turn("第一版答复"),
        make_turn("按补充改过"),
        steers=[[], [dict(STEER)]],
    )

    assert outcome.text == "按补充改过"
    assert [message["content"] for message in messages] == [
        "读 a.txt",
        "第一版答复",
        STEER["content"],
        "按补充改过",
    ]
    assert len(requests) == 2


def test_two_steers_keep_their_order_and_are_not_merged():
    _outcome, messages, _emitted, _requests = drive(
        make_turn("好"),
        steers=[[{"role": "user", "content": "第一条"}, {"role": "user", "content": "第二条"}]],
    )

    assert [message["content"] for message in messages] == [
        "读 a.txt",
        "第一条",
        "第二条",
        "好",
    ]


def test_without_a_channel_nothing_changes():
    outcome, messages, _emitted, requests = drive(make_turn("答复"))

    assert outcome.text == "答复"
    assert [message["content"] for message in messages] == ["读 a.txt", "答复"]
    assert len(requests) == 1


def test_cancellation_wins_over_a_pending_steer():
    """取消是另外一条通道：检查点先跑，被取消的 run 不会先把补充吃进来。"""
    delivered: list[dict] = []

    def take() -> list[dict]:
        delivered.append(dict(STEER))
        return [dict(STEER)]

    state = RunState.for_run(steers=take)
    state.cancel("user")
    spec = RunSpec.resolve(config=CONFIG, chat=ScriptedChat(make_turn("不该跑到这里")), registry={})

    try:
        Run([dict(USER)], spec, state=state).run()
        raise AssertionError("被取消的运行必须抛 RunCancelled")
    except RunCancelled:
        pass
    assert delivered == []
