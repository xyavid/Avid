"""Event payload fields the frontend reads must exist on the events the kernel really sends.

The REST half of this invariant is ``tests/test_wire_contract.py``; this is the event half, and it
was missing: event *names* were compared, payload *fields* were not, so renaming one left the
frontend degrading silently (``String(undefined)``, an empty card) with nothing going red. Fields
are compared against real payloads from scripted sessions rather than a second hand-written list,
so what is checked is what the kernel actually emits.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support import (
    RecordingTools,
    ScriptedChat,
    create_session,
    make_turn,
    new_session,
    tool_call,
    wait_for,
    wait_terminal,
)

from avid.agent.events import (
    ASSISTANT_DELTA,
    EVENT_TYPES,
    REASONING_DELTA,
    RUN_FAILED,
)
from avid.providers.client import LLMError
from avid.services import Services
from avid.web import create_app

ROOT = Path(__file__).resolve().parents[1]
TYPES_TS = ROOT / "web" / "src" / "events" / "types.ts"

_BLOCK = re.compile(r"// EVENT_FIELDS:BEGIN(.*?)// EVENT_FIELDS:END", re.S)
_MEMBER = re.compile(r"^\s*([a-z_]+):\s*(.+?)\s*$")
_FIELD = re.compile(r"'([a-z_0-9]+)'")

#: Declared but not reachable from the runs below, with the reason. An entry here is a gap in the
#: evidence, not a licence: the field set is the same one the other two terminal events are checked
#: against, and those two are observed.
NOT_COVERED = {
    "run_cancelled": "取消要卡住读历史那一步才抓得住，字段集与另两个终态相同（那两个已实测）",
}


def declared_fields() -> dict[str, set[str]]:
    """The frontend's per-event field declaration, parsed out of the marked block."""
    block = _BLOCK.search(TYPES_TS.read_text(encoding="utf-8"))
    assert block is not None, "types.ts 缺少 EVENT_FIELDS:BEGIN / EVENT_FIELDS:END 标记块"
    declared: dict[str, set[str]] = {}
    for line in block.group(1).splitlines():
        match = _MEMBER.match(line)
        if match:
            declared[match.group(1)] = set(_FIELD.findall(match.group(2)))
    return declared


def test_every_event_type_is_declared():
    """A new event must be added to the declaration too, with an explicit `never` if unused."""
    assert set(declared_fields()) == set(EVENT_TYPES)


def build(root, chat, tools=None, **kwargs) -> Services:
    return Services(root=root / ".avid" / "sessions", chat=chat, tool_registry=tools, **kwargs)


def payloads(services: Services, run_id: str) -> list:
    """Every event the run buffered, deltas included (they never reach a durable replay)."""
    return list(services.runs.get(run_id).events)


def run_to_end(services: Services, prompt: str = "跑一下"):
    record = services.runs.start(new_session(services), prompt)
    assert wait_terminal(record), f"运行没结束：{record.status}"
    return record


def answered_run(sandbox, decision: str) -> list:
    """One run that stops at an approval and is answered; the tool runs or the call is denied."""
    # Destructive so the gate asks; RecordingTools stands in for bash, so nothing is executed.
    chat = ScriptedChat(
        make_turn("", [tool_call("bash", '{"command": "rm -rf /"}')]), make_turn("好了")
    )
    tools = RecordingTools()
    services = build(sandbox, chat, tools.registry("bash"))
    client = TestClient(create_app(services=services), base_url="http://127.0.0.1:8765")
    try:
        session_id = create_session(client).json()["id"]
        created = client.post(f"/api/sessions/{session_id}/runs", json={"prompt": "跑一下"})
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        assert wait_for(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
        ), "运行没停在审批上"
        pending = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"]
        assert len(pending) == 1, pending
        answered = client.post(
            f"/api/runs/{run_id}/approvals/{pending[0]['approval_id']}",
            json={"decision": decision},
        )
        assert answered.status_code == 200, answered.text
        assert wait_for(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("finished", "failed")
        ), "答复之后运行没结束"
        return payloads(services, run_id)
    finally:
        services.close()


def raising_chat(exc: Exception):
    def chat(config, messages, **kwargs):
        raise exc

    return chat


@pytest.fixture
def observed(sandbox) -> dict[str, list[dict]]:
    """Real payloads per event type, gathered from the paths whose fields the frontend reads."""
    seen: dict[str, list[dict]] = defaultdict(list)
    services: list[Services] = []

    def keep(events) -> None:
        for event in events:
            seen[event.type].append(event.data)

    # A plain round with a read-only tool: the message, tool and status events.
    plain = build(sandbox, ScriptedChat(
        make_turn("", [tool_call("read_file", '{"path": "f.txt"}')]), make_turn("读完了")
    ), RecordingTools().registry("read_file"))
    services.append(plain)
    keep(payloads(plain, run_to_end(plain).run_id))

    # Both delta tiers, emitted on the live path (they are never persisted or replayed).
    deltas = build(sandbox, ScriptedChat(make_turn("答")))
    services.append(deltas)
    record = deltas.runs.start(new_session(deltas), "跑")
    assert wait_terminal(record)
    for kind, text in ((ASSISTANT_DELTA, "半个句子"), (REASONING_DELTA, "它在想")):
        deltas.runs.emit_delta(record, deltas.runs, text, event_type=kind)
    keep(payloads(deltas, record.run_id))

    # Both approval outcomes: the tool runs, or the call is denied.
    keep(answered_run(sandbox, "allow"))
    keep(answered_run(sandbox, "deny"))

    # A failure is its own terminal shape, with fields the other terminal events do not carry.
    failed = build(sandbox, raising_chat(LLMError("模型挂了")))
    services.append(failed)
    keep(payloads(failed, run_to_end(failed).run_id))

    for instance in services:
        instance.close()
    return seen


def test_the_frontend_never_declares_a_field_the_kernel_does_not_send(observed):
    """The one direction that matters: a declared field nobody sends is a silent degradation."""
    problems: list[str] = []
    for name, fields in declared_fields().items():
        if not fields:
            continue
        got = observed.get(name)
        if not got:
            if name not in NOT_COVERED:
                problems.append(f"{name}：断言里没有任何真实事件，声明的字段无从检验")
            continue
        sent = set().union(*(payload.keys() for payload in got))
        missing = fields - sent
        if missing:
            problems.append(f"{name}：内核没发 {sorted(missing)}（实际字段 {sorted(sent)}）")
    assert problems == [], "事件 payload 与前端声明不一致：\n" + "\n".join(problems)


def test_the_exemptions_are_still_exempt(observed):
    """An exemption that has become observable is stale and should be deleted."""
    stale = sorted(name for name in NOT_COVERED if observed.get(name))
    assert stale == [], f"这些事件已被观察到，可以从 NOT_COVERED 删掉：{stale}"


def test_terminal_events_all_carry_the_usage_snapshot(observed):
    """The frontend's context ring reads `usage` off whichever terminal event arrives first."""
    for name in (RUN_FAILED, "run_finished"):
        assert observed[name], f"{name} 没有实测样本"
        assert all("usage" in payload for payload in observed[name]), name
