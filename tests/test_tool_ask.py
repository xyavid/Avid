"""ask_user：问一句（或给选项）、没人答就降级、以及在真流水线里挂起与回传。

「没答」与「被拒绝」是两件事——这组用例把这条分寸钉住：通道缺席/超时/取消都回一句
「没有回答 + 基于现有信息继续」，而不是让运行失败或替用户编一个答案。
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
    """只有 question 通道的最小 run state（工具只读这一个属性）。"""

    def __init__(self, channel=None) -> None:
        self.question = channel


class Channel:
    """记录被问了什么，并按剧本作答。"""

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


# ---------------- 真流水线：挂起 → 作答 → 回传 ----------------


class Bench:
    """一张待决表 + 它发出的事件与状态，供用例断言。"""

    def __init__(self, timeout: float) -> None:
        self.events: list[tuple[str, dict]] = []
        self.statuses: list[str] = []
        self.flags = {"cancelled": False}
        self.table = ApprovalTable(
            # 形参换个名字：事件名本身就是第一个位置参数，叫 kind 会与载荷里的 kind 撞车。
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

    assert bench.table.answer(pending_id, "在").accepted is False  # 同答案幂等
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


# ---------------- 走真运行：模型提问 → 界面作答 → 运行继续 ----------------


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
        # 答案真的回到了模型手里：它据此作答（剧本第二轮直接收尾）。
        detail = client.get(f"/api/sessions/{session_id}").json()
        assert detail["message_count"] >= 2
        assert bound_workspace(services)
    finally:
        services.close()
