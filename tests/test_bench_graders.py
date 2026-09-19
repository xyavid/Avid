"""AvidBench 判定器的单测：五种原语各要有正例、反例与"判定器自己出错"的路径。

判定器是仪器的刻度。刻度错了，后面所有数字都没有意义。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.avidbench.graders import (
    GraderError,
    is_tool_failure,
    normalize,
    run_grader,
    run_graders,
    validate_spec,
)


def grade(spec: dict, workspace: Path, answer: str = "", timeout: float = 10.0):
    return run_grader(spec, workspace=workspace, answer=answer, timeout_seconds=timeout)


# ---------------- 归一化 ----------------

def test_normalize_ignores_case_space_and_commas():
    assert normalize("Sum = 4,176") == "sum=4176"
    assert normalize("SUM=4176") == normalize("sum = 4,176")
    assert normalize("TODO 总数 = 6") == "todo总数=6"


def test_is_tool_failure_reads_the_two_prefixes():
    assert is_tool_failure("错误：路径越界")
    assert is_tool_failure("  工具执行失败：bash（timeout）")
    assert not is_tool_failure("正常结果：137")


# ---------------- command ----------------

def test_command_grader_checks_exit_code_and_stdout(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    assert grade({"kind": "command", "run": "grep -q hello a.txt"}, tmp_path).passed
    assert not grade({"kind": "command", "run": "grep -q nope a.txt"}, tmp_path).passed
    ok = grade(
        {"kind": "command", "run": "cat a.txt", "stdout_contains": ["hello"]}, tmp_path
    )
    assert ok.passed
    bad = grade(
        {"kind": "command", "run": "cat a.txt", "stdout_contains": ["bye"]}, tmp_path
    )
    assert not bad.passed and "缺少" in bad.detail


def test_command_grader_reports_timeout_instead_of_hanging(tmp_path: Path):
    result = grade({"kind": "command", "run": "sleep 5"}, tmp_path, timeout=0.1)
    assert not result.passed and "超时" in result.detail


def test_command_grader_respects_expect_exit(tmp_path: Path):
    assert grade({"kind": "command", "run": "exit 3", "expect_exit": 3}, tmp_path).passed
    assert not grade({"kind": "command", "run": "exit 3"}, tmp_path).passed


# ---------------- file_* ----------------

def test_file_contains_and_missing_file(tmp_path: Path):
    (tmp_path / "x.txt").write_text("abc\ndef\n", encoding="utf-8")
    assert grade({"kind": "file_contains", "path": "x.txt", "text": ["abc", "def"]}, tmp_path).passed
    assert not grade({"kind": "file_contains", "path": "x.txt", "text": "zzz"}, tmp_path).passed
    missing = grade({"kind": "file_contains", "path": "nope.txt", "text": "a"}, tmp_path)
    assert not missing.passed and "判定器出错" in missing.detail


def test_file_equals_ignores_trailing_newline_only(tmp_path: Path):
    (tmp_path / "x.txt").write_text("line\n", encoding="utf-8")
    assert grade({"kind": "file_equals", "path": "x.txt", "text": "line"}, tmp_path).passed
    assert not grade({"kind": "file_equals", "path": "x.txt", "text": "line "}, tmp_path).passed


def test_paths_outside_workspace_are_rejected(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = grade({"kind": "file_contains", "path": "../outside.txt", "text": "secret"}, tmp_path)
    assert not result.passed and "越出工作区" in result.detail


# ---------------- json_path_equals ----------------

def test_json_pointer_walks_dicts_and_lists(tmp_path: Path):
    (tmp_path / "c.json").write_text(
        json.dumps({"a": {"b": [1, {"c": "v"}]}}), encoding="utf-8"
    )
    assert grade({"kind": "json_path_equals", "path": "c.json", "pointer": "a.b.1.c", "value": "v"}, tmp_path).passed
    assert not grade({"kind": "json_path_equals", "path": "c.json", "pointer": "a.b.0", "value": 2}, tmp_path).passed
    missing = grade({"kind": "json_path_equals", "path": "c.json", "pointer": "a.z", "value": 1}, tmp_path)
    assert not missing.passed and "不存在" in missing.detail


def test_json_pointer_keeps_booleans_apart_from_integers(tmp_path: Path):
    (tmp_path / "c.json").write_text(json.dumps({"enabled": 1}), encoding="utf-8")
    result = grade({"kind": "json_path_equals", "path": "c.json", "pointer": "enabled", "value": True}, tmp_path)
    assert not result.passed, "True 与 1 不能互认"


def test_json_grader_reports_broken_json(tmp_path: Path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    result = grade({"kind": "json_path_equals", "path": "bad.json", "pointer": "", "value": {}}, tmp_path)
    assert not result.passed and "不是合法 JSON" in result.detail


# ---------------- answer_contains ----------------

def test_answer_contains_normalizes_both_sides():
    spec = {"kind": "answer_contains", "text": ["sum=4176"]}
    assert grade(spec, Path("."), "y 列之和：Sum = 4,176。").passed
    assert not grade(spec, Path("."), "sum=4177").passed


# ---------------- 声明校验 ----------------

@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"kind": "nope"},
        {"kind": "command"},
        {"kind": "command", "run": "true", "unknown": 1},
        {"kind": "file_contains", "path": "a"},
        {"kind": "file_contains", "path": "", "text": "a"},
        {"kind": "answer_contains", "text": 3},
        {"kind": "command", "run": "true", "expect_exit": True},
    ],
)
def test_validate_spec_rejects_bad_declarations(spec):
    with pytest.raises(GraderError):
        validate_spec(spec)


def test_run_graders_keeps_declaration_order(tmp_path: Path):
    specs = [
        {"kind": "command", "run": "true"},
        {"kind": "answer_contains", "text": "abc"},
    ]
    results = run_graders(specs, workspace=tmp_path, answer="abc")
    assert [item.kind for item in results] == ["command", "answer_contains"]
    assert all(item.passed for item in results)
