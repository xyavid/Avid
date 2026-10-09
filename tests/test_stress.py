"""stress: long-session and performance gates, skipped by default (``pytest -m stress``).

These paths could regress in complexity rather than correctness:
* Session list replaying every file: 20 sessions of 5.9 MB took 54 ms, and first paint grows to
  seconds with more sessions.
* Every delta rescanning the event buffer: 8000 chunks took 1.05 s on the thread that reads the
  model SSE.

Thresholds carry a 10-30x margin and must catch complexity (quadratic work, per-session full
replays), not a few percent of jitter; prefer asserting the mechanism over the clock.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from avid.agent.events import RUN_STARTED
from avid.services import Services
from avid.services.runs import RunRecord, RunRegistry
from avid.session import (
    STORAGE_VERSION,
    JsonlSessionMetadata,
    JsonlSessionRepo,
)
from avid.session.jsonl import (
    JsonlHeader,
    JsonlStorage,
    encode_header,
    encode_transaction,
)
from avid.session.types import CommittedEntry, CommittedValueSet, NewEntry
from avid.session.values import BRANCH_TIP_NS, SESSION_NAME_NS

pytestmark = pytest.mark.stress

USER = {"role": "user", "content": "一"}
ASSISTANT = {"role": "assistant", "content": "二"}


def write_session_file(
    directory: Path, session_id: str, messages: int, *, payload: int = 2000
) -> Path:
    """Write JSONL lines directly, skipping the recorder's per-message fsync so the fixture
    itself is not the bottleneck."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"2026-01-01T00-00-00-000_{session_id}.jsonl"
    lines = [encode_header(JsonlHeader(session_id, STORAGE_VERSION, 1))]
    parent: str | None = None
    for index in range(1, messages + 1):
        entry_id = f"{session_id}-e{index}"
        message = USER if index % 2 else {**ASSISTANT, "content": "二" * payload}
        lines.append(
            encode_transaction(
                [CommittedEntry(index, 1, NewEntry(entry_id, parent, message=message))]
            )
        )
        parent = entry_id
    lines.append(
        encode_transaction(
            [CommittedValueSet(messages + 1, BRANCH_TIP_NS, "main", parent or "")]
        )
    )
    lines.append(
        encode_transaction(
            [CommittedValueSet(messages + 2, SESSION_NAME_NS, "", f"会话 {session_id}")]
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_stress_session_list_does_not_replay_each_session(tmp_path, monkeypatch):
    """Listing must cost O(session count), not count x file size: no session file is opened."""
    sessions_root = tmp_path / "sessions"
    count = 200
    for index in range(count):
        write_session_file(sessions_root, f"stress-{index:03d}", messages=60)

    opened: list[str] = []
    real_open = JsonlStorage.open

    def counting_open(path, **kwargs):
        opened.append(str(path))
        return real_open(path, **kwargs)

    monkeypatch.setattr(JsonlStorage, "open", staticmethod(counting_open))
    services = Services(root=sessions_root)
    try:
        started = time.perf_counter()
        listed = services.sessions.list_sessions()
        elapsed = time.perf_counter() - started
    finally:
        services.close()

    assert len(listed) == count
    assert opened == [], "列表页不该重放会话"
    sample = next(item for item in listed if item["id"] == "stress-007")
    assert sample["message_count"] == 60
    assert sample["name"] == "会话 stress-007"
    assert elapsed < 1.5, f"{count} 个会话的列表花了 {elapsed:.2f}s（复杂度退化了？）"


def test_stress_delta_emit_stays_linear(tmp_path):
    """Delta accounting must stay O(1), not a full buffer scan per delta; the 0.5 s budget only
    catches complexity, not jitter."""
    registry = RunRegistry(None, buffer_size=512)
    record = RunRecord(run_id="stress", session_id="s", started_at=0)
    registry.emit(record, RUN_STARTED)

    total = 8000
    started = time.perf_counter()
    for _ in range(total):
        registry.emit_delta(record, registry, "tok")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5, f"{total} 个 delta 花了 {elapsed:.2f}s（记账复杂度退化了？）"
    assert record.absolute_index() == record.dropped + len(record.events)


def test_stress_long_session_summarize_and_replay_stay_usable(tmp_path):
    """Both read paths on a long session stay usable: summarize is one read plus a tail window
    and replay parses line by line, neither degrading to a full scan."""
    sessions_root = tmp_path / "sessions"
    path = write_session_file(sessions_root, "long", messages=5000)

    repo = JsonlSessionRepo(sessions_root)
    try:
        started = time.perf_counter()
        summary = repo.summarize(repo.list()[0])
        summarize_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        session = repo.open(
            JsonlSessionMetadata(
                id="long",
                created_at=1,
                storage_version=STORAGE_VERSION,
                path=path,
            )
        )
        open_elapsed = time.perf_counter() - started
        session.close()
    finally:
        repo.close()

    assert summary.message_count == 5000
    assert summarize_elapsed < 1.0, f"摘要花了 {summarize_elapsed:.2f}s"
    assert open_elapsed < 3.0, f"重放 5000 条花了 {open_elapsed:.2f}s"
