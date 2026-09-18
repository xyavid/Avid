"""一致性套件的两个后端入口：同一批用例，两个实现。

用例本体住在 ``session_cases.py``，这里只负责造仓库、跑用例、收尾。
参数化 id 形如 ``memory/lifecycle-...``，两个后端成对出现。
"""

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
    """每次都往前走的时钟：id、created_at、timestamp 因此完全确定。"""
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
    """带归属的仓库：一致性用例里要能表达"这是哪个工作区的库"。"""
    generator = UuidV7Generator(clock)
    if backend == "memory":
        return MemorySessionRepo(
            now=clock, id_generator=generator, workspace=workspace
        )
    return JsonlSessionRepo(
        root, now=clock, id_generator=generator, workspace=workspace
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_session_claiming_another_workspace_is_refused(backend, tmp_path):
    """归属护栏两个后端必须同义：文件后端早就有，内存后端以前什么都不校验。"""
    clock = ticking_clock()
    repo = make_workspace_repo(backend, tmp_path / "sessions", clock, "w-mine")
    session = repo.create(id="s1", workspace="w-other")
    metadata = session.metadata
    assert metadata.workspace == "w-other"
    session.close()

    with pytest.raises(SessionStorageError):
        repo.open(metadata)
    with pytest.raises(SessionStorageError):
        repo.delete(metadata)
    repo.close()
