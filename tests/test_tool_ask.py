"""ask_user: ask a question (optionally with choices), degrade when nobody answers, and suspend
then resume inside a real pipeline.

"No answer" and "denial" are different: a missing channel, a timeout, and a cancelled run all return
"no answer, continue with what you have" instead of failing the run or inventing a reply.
"""

from __future__ import annotations

import threading
import time

import pytest
from support import ScriptedChat, make_turn, tool_call, wait_for

from avid.agent.tools.interaction import (
    MAX_OPTION_CHARS,
    MAX_OPTIONS,
    NO_ANSWER,
    NO_CHANNEL,
    ask_user,
)
from avid.services.approvals import KIND_QUESTION, ApprovalTable


class State:
    """Minimal run state with only the question channel (the tool reads just this attribute)."""

    def __init__(self, channel=None) -> None:
        self.question = channel


class Channel:
    """Records what was asked and answers on cue."""

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.seen: list[tuple[str, tuple[str, ...]]] = []

    def __call__(self, question: str, options: tuple[str, ...]) -> str | None:
        self.seen.append((question, options))
        return self.answer


def test_an_answer_comes_back_to_the_model():
    channel = Channel("用方案 B")

    out = ask_user({"question": "选哪个方案？", "options": ["方案 A", "方案 B"]}, state=State(channel))

    assert out == "用户回答：用方案 B"
    assert channel.seen == [("选哪个方案？", ("方案 A", "方案 B"))]


def test_no_channel_degrades_instead_of_failing():
    out = ask_user({"question": "在吗？"}, state=State(None))

    assert out == NO_CHANNEL
    assert "不要重复提问" in out and "继续" in out


def test_nobody_answering_is_not_a_denial():
    out = ask_user({"question": "在吗？"}, state=State(Channel(None)))

    assert out == NO_ANSWER
    assert "没有回答" in out and "拒绝" not in out


def test_a_blank_answer_counts_as_no_answer():
    assert ask_user({"question": "在吗？"}, state=State(Channel("   "))) == NO_ANSWER


def test_a_missing_question_is_a_readable_error():
    assert ask_user({}, state=State(Channel("x"))).startswith("错误：缺少参数 question")


def test_options_are_cleaned_and_capped():
    channel = Channel("好")
    many = [f"选项{index}" for index in range(MAX_OPTIONS + 3)]

    ask_user({"question": "选一个", "options": [*many, "  ", "字" * 200]}, state=State(channel))

    question, options = channel.seen[0]
    assert question == "选一个"
    assert len(options) <= MAX_OPTIONS
    assert all(len(item) <= MAX_OPTION_CHARS for item in options)
    assert "" not in options


def test_the_question_is_collapsed_and_capped():
    channel = Channel("好")

    ask_user({"question": "  多\n行   问题" + "字" * 2000}, state=State(channel))

    question, _ = channel.seen[0]
    assert "\n" not in question and len(question) <= 500


def test_the_tool_is_exclusive_and_never_writes():
    from avid.agent.tools import specs

    spec = next(item for item in specs() if item.name == "ask_user")
    assert spec.concurrency == "exclusive"
    assert spec.writes is False


# ---- real pipeline: suspend -> answer -> resume ----


class Bench:
    """A pending table plus the events and statuses it emits, for assertions."""

    def __init__(self, timeout: float) -> None:
        self.events: list[tuple[str, dict]] = []
        self.statuses: list[str] = []
        self.flags = {"cancelled": False}
        self.table = ApprovalTable(
            # Not "kind": the event name comes first positionally and the payload already has kind.
            emit=lambda event, **payload: self.events.append((event, payload)),
            set_status=self.statuses.append,
            is_cancelled=lambda: self.flags["cancelled"],
            timeout=timeout,
        )

    def pending(self):
        return self.table.pending()


def make_table(timeout: float = 5.0) -> Bench:
    return Bench(timeout)


def test_ask_blocks_until_someone_answers():
    bench = make_table()
    answers: list[str | None] = []

    worker = threading.Thread(
        target=lambda: answers.append(bench.table.ask("要不要继续？", ("要", "不要")))
    )
    worker.start()
    assert wait_for(lambda: len(bench.pending()) == 1, 3), "提问没有挂起"
    pending = bench.pending()[0]
    assert pending.kind == KIND_QUESTION and pending.options == ("要", "不要")

    resolved = bench.table.answer(pending.id, "要")

    worker.join(3)
    assert resolved.accepted is True
    assert answers == ["要"]
    assert [event for event, _ in bench.events] == ["approval_requested", "approval_resolved"]
    assert bench.events[0][1]["kind"] == KIND_QUESTION
    assert bench.events[0][1]["options"] == ["要", "不要"]
    assert bench.events[1][1]["answer"] == "要"


def test_asking_twice_is_idempotent_and_a_second_answer_conflicts():
    from avid.services.errors import ApprovalConflict

    bench = make_table()
    worker = threading.Thread(target=lambda: bench.table.ask("在吗？"))
    worker.start()
    assert wait_for(lambda: len(bench.pending()) == 1, 3)
    pending_id = bench.pending()[0].id
    bench.table.answer(pending_id, "在")
    worker.join(3)

    assert bench.table.answer(pending_id, "在").accepted is False  # the same answer is idempotent
    with pytest.raises(ApprovalConflict):
        bench.table.answer(pending_id, "不在")


def test_a_timeout_returns_no_answer_not_a_denial():
    bench = make_table(timeout=0.3)
    started = time.monotonic()

    assert bench.table.ask("在吗？") is None

    assert time.monotonic() - started < 3
    assert bench.pending() == []


def test_a_cancelled_run_gets_no_answer():
    bench = make_table(timeout=30)
    bench.flags["cancelled"] = True

    assert bench.table.ask("在吗？") is None


def test_an_approval_id_cannot_be_answered_as_a_question():
    from avid.services.errors import ApprovalConflict

    bench = make_table()
    worker = threading.Thread(
        target=lambda: bench.table.request("bash", {"command": "rm x"}, "危险")
    )
    worker.start()
    assert wait_for(lambda: len(bench.pending()) == 1, 3)
    approval_id = bench.pending()[0].id
    bench.table.resolve(approval_id, "deny")
    worker.join(3)

    with pytest.raises(ApprovalConflict):
        bench.table.answer(approval_id, "随便写点什么")


# ---- full run: model asks -> UI answers -> run continues ----


def test_a_run_suspends_on_ask_user_and_continues_with_the_answer(tmp_path):
    from fastapi.testclient import TestClient
    from support import bound_workspace, create_session

    from avid.services import Services
    from avid.web import create_app

    chat = ScriptedChat(
        make_turn("", [tool_call("ask_user", '{"question": "用哪个名字？", "options": ["甲", "乙"]}', "c1")]),
        make_turn("好，就用甲"),
    )
    services = Services(workspace_root=tmp_path, chat=chat)
    try:
        client = TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        session_id = create_session(client).json()["id"]
        run = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "起个名字"})
        assert run.status_code == 201, run.text
        run_id = run.json()["run_id"]

        assert wait_for(lambda: client.get(f"/api/runs/{run_id}/approvals").json()["approvals"], 5)
        pending = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"][0]
        assert pending["kind"] == "question"
        assert pending["options"] == ["甲", "乙"]
        assert pending["reason"] == "用哪个名字？"

        answered = client.post(
            f"/api/runs/{run_id}/approvals/{pending['approval_id']}", json={"answer": "甲"}
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["accepted"] is True

        assert wait_for(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "finished", 5
        )
        # The answer really reached the model: it answers from it (the script's second turn ends).
        detail = client.get(f"/api/sessions/{session_id}").json()
        assert detail["message_count"] >= 2
        assert bound_workspace(services)
    finally:
        services.close()
