"""评测集的准入检查：这些不变量错了，跑出来的数字就没有解释价值。

零模型成本——只读文件，不调用模型。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.avidbench.case import (
    CATEGORIES,
    FIXTURES_ROOT,
    CaseError,
    case_paths,
    load_case,
    load_cases,
)
from benchmarks.avidbench.graders import KINDS
from benchmarks.avidbench.workspace import BOOKKEEPING, manifest

EXPECTED_CATEGORY_COUNTS = {
    "basic": 4,
    "long_horizon": 3,
    "recovery": 2,
    "subagent": 1,
    "task": 1,
    "session": 1,
}


def test_eval_set_shape():
    cases = load_cases()
    assert len(cases) == sum(EXPECTED_CATEGORY_COUNTS.values())
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    assert counts == EXPECTED_CATEGORY_COUNTS
    assert len({case.id for case in cases}) == len(cases)


def test_every_case_is_decidable():
    for case in load_cases():
        assert case.graders, f"{case.id} 没有判定器"
        assert case.prompt.strip()
        assert "回答格式" in case.prompt, f"{case.id} 的 prompt 没写回答格式，判定会脆"
        assert case.limits.max_rounds >= 1
        assert case.limits.timeout_seconds > 0
        assert {spec["kind"] for spec in case.graders} <= set(KINDS)


def test_fixtures_are_read_only_and_clean():
    for case in load_cases():
        root = FIXTURES_ROOT / case.fixture
        assert root.is_dir(), case.id
        assert manifest(root), f"{case.id} 的 fixture 是空的"
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            assert relative.parts[0] not in BOOKKEEPING, (
                f"{case.id} 的 fixture 里混进了簿记目录 {relative}："
                "fixtures/ 必须保持干净，否则初始状态不可重放"
            )


def test_session_case_scores_the_second_round_only():
    case = load_cases(ids=["m01_fact_carryover"])[0]
    assert case.followup
    assert case.scored_phase == 1
    # 第二轮要的那个事实（VER- 前缀）只能来自第一轮对话，工作区里查不到
    fixture_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (FIXTURES_ROOT / case.fixture).rglob("*")
        if path.is_file()
    )
    assert "VER-" not in fixture_text


def test_other_cases_have_no_followup():
    for case in load_cases():
        if case.id != "m01_fact_carryover":
            assert case.followup is None


def test_load_cases_can_select_and_reports_unknown_ids():
    assert [case.id for case in load_cases(ids=["b02_column_sum"])] == ["b02_column_sum"]
    with pytest.raises(CaseError):
        load_cases(ids=["b02_column_sum", "nope"])


def test_case_paths_finds_one_file_per_case():
    paths = case_paths()
    assert len(paths) == len(load_cases())
    assert all(path.suffix == ".toml" for path in paths)


def _write_case(tmp_path: Path, body: str, name: str = "x01_demo") -> Path:
    path = tmp_path / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


_GOOD_GRADER = '[[graders]]\nkind = "answer_contains"\ntext = "a"\n'

#: 每一行都要被拒。label 进 pytest 的用例名，坏在哪一眼就能看出。
BAD_CASES = {
    "id 与文件名不一致": f'id = "other"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "p"\n{_GOOD_GRADER}',
    "未知 category": f'id = "x01_demo"\ncategory = "nope"\nfixture = "b01_largest_file"\nprompt = "p"\n{_GOOD_GRADER}',
    "fixture 不存在": f'id = "x01_demo"\ncategory = "basic"\nfixture = "no_such_dir"\nprompt = "p"\n{_GOOD_GRADER}',
    "没有 grader": 'id = "x01_demo"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "p"\n',
    "空 prompt": f'id = "x01_demo"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "  "\n{_GOOD_GRADER}',
    "limits 有未知键": f'id = "x01_demo"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "p"\n[limits]\nnope = 1\n{_GOOD_GRADER}',
    "grader 不合法": 'id = "x01_demo"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "p"\n[[graders]]\nkind = "nope"\n',
}


@pytest.mark.parametrize(("label", "body"), list(BAD_CASES.items()), ids=list(BAD_CASES))
def test_load_case_rejects_bad_files(label: str, body: str, tmp_path: Path):
    with pytest.raises(CaseError, match="."):
        load_case(_write_case(tmp_path, body))


def test_all_categories_have_at_least_one_case():
    used = {case.category for case in load_cases()}
    assert used == set(CATEGORIES)
