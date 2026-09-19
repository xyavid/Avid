"""评测集的准入检查：这些不变量错了，跑出来的数字就没有解释价值。

零模型成本——只读文件，不调用模型。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.avidbench.case import (
    CATEGORIES,
    FIXTURES_ROOT,
    TIERS,
    CaseError,
    case_paths,
    load_case,
    load_cases,
    load_suite,
    suites,
)
from benchmarks.avidbench.graders import KINDS, run_graders
from benchmarks.avidbench.workspace import BOOKKEEPING, manifest, materialized

#: 每个 case 集版本各有多少条、按类别怎么分。**已冻结的版本不许再改**——
#: 改这里就等于宣布旧基线作废（所以加版本，不是改旧版本）。
EXPECTED_BY_SUITE = {
    "v0": {"basic": 4, "long_horizon": 3, "recovery": 2, "subagent": 1, "task": 1, "session": 1},
    "v1": {"long_horizon": 3, "task": 4, "subagent": 1, "recovery": 1},
}


def test_eval_set_shape():
    for suite, expected in EXPECTED_BY_SUITE.items():
        cases = load_suite(suite)
        assert len(cases) == sum(expected.values()), suite
        counts: dict[str, int] = {}
        for case in cases:
            counts[case.category] = counts.get(case.category, 0) + 1
        assert counts == expected, suite
    every = load_suite("all")
    assert len(every) == sum(sum(counts.values()) for counts in EXPECTED_BY_SUITE.values())
    assert len({case.id for case in every}) == len(every), "case id 跨版本也必须唯一"


def test_suites_are_discovered_from_directories():
    assert suites() == sorted(EXPECTED_BY_SUITE)
    with pytest.raises(CaseError):
        load_suite("v9")


def test_difficulty_is_declared_for_the_new_suite_only():
    """v1 的每条都要标 tier；v0 已冻结，一个字段都不许动。"""
    for case in load_suite("v1"):
        assert case.tier in TIERS, f"{case.id} 没标 tier"
    assert {case.tier for case in load_suite("v1")} >= {3, 4, 5}, "v1 要有真正的难度分层"
    for case in load_suite("v0"):
        assert case.tier is None, f"{case.id} 属于已冻结的 v0，不该被改"


def test_suites_are_selectable_by_id_within_one_version():
    assert [case.id for case in load_suite("v1", ids=["c07_many_modules"])] == ["c07_many_modules"]
    assert len(load_suite("v1", ids=None)) == sum(EXPECTED_BY_SUITE["v1"].values())


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
    "tier 越界": f'id = "x01_demo"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "p"\ntier = 9\n{_GOOD_GRADER}',
    "tier 不是整数": f'id = "x01_demo"\ncategory = "basic"\nfixture = "b01_largest_file"\nprompt = "p"\ntier = "3"\n{_GOOD_GRADER}',
}


@pytest.mark.parametrize(("label", "body"), list(BAD_CASES.items()), ids=list(BAD_CASES))
def test_load_case_rejects_bad_files(label: str, body: str, tmp_path: Path):
    with pytest.raises(CaseError, match="."):
        load_case(_write_case(tmp_path, body))


def test_difficulty_graders_stay_decidable():
    """难度 case 的判定器也必须全是确定性的——难度不许靠 LLM judge 来判。"""
    for case in load_suite("v1"):
        assert {spec["kind"] for spec in case.graders} <= set(KINDS)
        assert "回答格式" in case.prompt, f"{case.id} 没写回答格式"
        assert case.limits.max_rounds >= 12, f"{case.id} 的轮数上限对难度 case 太紧"


def test_fixture_invariant_graders_pass_on_a_pristine_workspace():
    """不依赖回答的那些判定器，必须在**没跑过模型**的 fixture 上就通过。

    它们是「fixture 不变量」：一旦有人改了 fixture 而没改真值，这条会先红——不用等到
    花完真模型的钱才发现判定器自己写错了。
    """
    for case in load_cases():
        specs = [spec for spec in case.graders if spec["kind"] != "answer_contains"]
        if not specs:
            continue
        with materialized(case) as root:
            results = run_graders(specs, workspace=root, answer="")
        failed = [(item.kind, item.detail) for item in results if not item.passed]
        assert not failed, f"{case.id} 的 fixture 不变量在干净工作区上就失败了：{failed}"


def test_all_categories_have_at_least_one_case():
    used = {case.category for case in load_cases()}
    assert used == set(CATEGORIES)
