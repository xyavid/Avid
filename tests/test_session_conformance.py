"""Conformance suite entry: the cases in ``session_cases.py`` run against both backends."""


from __future__ import annotations

import itertools

import pytest
from session_cases import all_cases

from avid.session import (
    JsonlSessionRepo,
    MemorySessionRepo,
    SessionStorageError,
    UuidV7Generator,
)

BACKENDS = ("memory", "jsonl")


def ticking_clock(start: int = 1_700_000_000_000, step: int = 1_000):
    """A clock that always advances, making ids, created_at and timestamps deterministic."""
    counter = itertools.count(start, step)
    return lambda: next(counter)


def make_repo(backend: str, root, clock):
    generator = UuidV7Generator(clock)
    if backend == "memory":
        return MemorySessionRepo(now=clock, id_generator=generator)
    return JsonlSessionRepo(root, now=clock, id_generator=generator)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "case", all_cases(), ids=lambda case: f"{case.group}-{case.name}"
)
def test_session_repo_contract(backend, case, tmp_path):
    clock = ticking_clock()
    repo = make_repo(backend, tmp_path / "sessions", clock)
    try:
        case.run(repo)
    finally:
        repo.close()


def make_workspace_repo(backend: str, root, clock, workspace: str):
    """A repo with a workspace label, so a case can express which workspace owns it."""
    generator = UuidV7Generator(clock)
    if backend == "memory":
        return MemorySessionRepo(
            now=clock, id_generator=generator, workspace=workspace
        )
    return JsonlSessionRepo(
        root, now=clock, id_generator=generator, workspace=workspace
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_session_from_outside_the_repository_is_refused(backend, tmp_path):
    """Both backends refuse open/delete for a foreign session; ownership is by file location
    (jsonl) or by the recorded label (memory)."""
    clock = ticking_clock()
    if backend == "jsonl":
        repo = make_workspace_repo(backend, tmp_path / "sessions", clock, "w-mine")
        foreign_repo = make_workspace_repo(backend, tmp_path / "elsewhere", clock, "w-other")
        session = foreign_repo.create(id="s1")
        session.close()
        metadata = session.metadata
        foreign_repo.close()
    else:
        repo = make_workspace_repo(backend, tmp_path / "sessions", clock, "w-mine")
        session = repo.create(id="s1", workspace="w-other")
        session.close()
        metadata = session.metadata

    with pytest.raises(SessionStorageError):
        repo.open(metadata)
    with pytest.raises(SessionStorageError):
        repo.delete(metadata)
    repo.close()
