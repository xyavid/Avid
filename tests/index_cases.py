"""Shared helpers for index cases: build real sessions and read back byte ranges.

Fixtures (store / indexer) live in conftest.py, since importing them would trip ruff's
F401/F811; only functions that import cleanly belong here.
"""

from __future__ import annotations

import json
from pathlib import Path

from avid.session import JsonlSessionRepo, SessionRecorder
from avid.session.values import session_name

ALPHA, BETA = "w-alpha", "w-beta"
WORKSPACES = {ALPHA: ("/ws/alpha", "alpha"), BETA: ("/ws/beta", "beta")}


def make_session(
    store: Path,
    *,
    workspace: str | None = ALPHA,
    session_id: str | None = None,
    messages: tuple[dict, ...] = (),
    name: str | None = None,
) -> Path:
    """Write a real session under the store; returns its file."""
    directory = store / (workspace or "unowned")
    repo = JsonlSessionRepo(directory, workspace=workspace)
    try:
        session = repo.create(id=session_id)
        recorder = SessionRecorder(session)
        for message in messages:
            recorder.on_message(message)
        if name is not None:
            session.set_value(session_name(), name)
        glob = f"*_{session.metadata.id}.jsonl"
    finally:
        repo.close()
    return next(directory.glob(glob))


def append_message(path: Path, message: dict, *, workspace: str | None = ALPHA) -> None:
    """Append one more committed entry to an existing session file."""
    repo = JsonlSessionRepo(path.parent, workspace=workspace)
    try:
        session = repo.open(next(item for item in repo.list() if item.id in path.name))
        SessionRecorder(session, branch="main").on_message(message)
    finally:
        repo.close()


def read_byte_range(path: Path, offset: int, length: int) -> dict:
    """The jump the index promises: byte range → the original record, parsed by the store codec."""
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(length).decode("utf-8")
    payload = json.loads(raw.strip())
    return payload if isinstance(payload, dict) else payload[0]
