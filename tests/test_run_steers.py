"""Where steers are delivered: only at step boundaries, landing as an ordinary user message.

Pins four contracts: delivery only between tool results and the next model request; a pending steer keeps a finishing run alive; runs without a channel behave exactly as before; and cancellation wins over a pending steer.
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
    """Run a script; steers is the list of pending batches, one batch handed out per take."""
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
    """A steer posted while a tool batch runs lands after the results and before the next model request."""
    outcome, messages, emitted, requests = drive(
        make_turn("", [tool_call("read_file")]),
        make_turn("好"),
        registry={"read_file": lambda arguments, **kwargs: "内容"},
        steers=[[], [dict(STEER)]],  # nothing at the first boundary yet; it arrives only after the file read
    )

    assert outcome.text == "好"
    # Structurally safe: tool results land first, the steer follows, then the next model request
    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "user", "assistant"]
    assert messages[3] == STEER
    # Visible in the second request (really in the model context; the last message is the rebuilt tail note)
    assert requests[1]["messages"][3] == STEER
    assert requests[1]["messages"][-1]["content"].startswith("[上下文]")
    # The on_message sink sees it too: that is how it becomes a session entry
    assert STEER in emitted


def test_a_steer_already_waiting_is_delivered_before_the_first_call():
    """A steer racing with run start enters the first batch and is visible to the first model request."""
    _outcome, messages, _emitted, requests = drive(
        make_turn("好"),
        steers=[[dict(STEER)]],
    )

    assert [message["role"] for message in messages] == ["user", "user", "assistant"]
    assert requests[0]["messages"][0] == USER
    assert requests[0]["messages"][1] == STEER


def test_a_steer_keeps_a_finishing_run_alive():
    """A steer waiting at the boundary keeps a finishing run alive instead of closing it."""
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
    """The cancellation checkpoint runs first, so a cancelled run never consumes the pending steer."""
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
