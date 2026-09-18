"""一致性套件的两个后端入口：同一批用例，两个实现。

用例本体住在 ``session_cases.py``，这里只负责造仓库、跑用例、收尾。
参数化 id 形如 ``memory/lifecycle-...``，两个后端成对出现。
"""

from __future__ import annotations

import itertools

import pytest
from session_cases import all_cases

from avid.session import JsonlSessionRepo, MemorySessionRepo, UuidV7Generator

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
