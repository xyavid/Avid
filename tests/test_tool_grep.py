"""grep_search: structured hits, concurrency safety, and one output shape from both code paths.

Assertions target the lines the model sees: workspace-relative paths, line numbers, and an honest
note when results are capped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from avid.agent.tools import search as search_module
from avid.agent.tools.search import MAX_RESULTS, grep_search


@pytest.fixture
def project(tmp_path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def handler():\n    pass\n\n# TODO: 会话存储\n", encoding="utf-8"
    )
    (tmp_path / "src" / "notes.md").write_text("会话存储在这里\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("// 会话存储\n", encoding="utf-8")
    (tmp_path / ".git" / "config").write_text("会话存储\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01" + "会话存储".encode() + b"\xff")
    return tmp_path


class State:
    """Minimal run state: the tool reads workspace_root only."""

    def __init__(self, root: Path) -> None:
        self.workspace_root = str(root)


def run(pattern: str, project: Path, **args) -> str:
    return grep_search({"pattern": pattern, "path": ".", **args}, state=State(project))


def test_finds_content_with_path_and_line(project):
    out = run("会话存储", project)

    assert "src/app.py:4: # TODO: 会话存储" in out
    assert "src/notes.md:1: 会话存储在这里" in out


def test_ignores_dependencies_and_dot_directories(project):
    """node_modules and .git stay out by default: noise, not answers."""
    out = run("会话存储", project)

    assert "node_modules" not in out
    assert ".git" not in out


def test_default_is_case_insensitive_and_can_be_tightened(project):
    assert "src/app.py" in run("todo", project)
    assert "未找到" in run("todo", project, case_sensitive=True)


def test_a_glob_limits_the_search(project):
    only_md = run("会话存储", project, glob="*.md")

    assert "src/notes.md" in only_md
    assert "app.py" not in only_md


def test_an_invalid_regex_is_a_readable_error(project):
    out = run("def (", project)

    assert out.startswith("错误：正则非法")


def test_no_match_says_so(project):
    assert run("完全没有出现过的词", project).startswith("未找到匹配")


def test_max_results_caps_the_output_and_says_it_is_partial(tmp_path):
    (tmp_path / "many.txt").write_text("\n".join(f"命中 {index}" for index in range(120)), encoding="utf-8")

    out = run("命中", tmp_path, max_results=5)

    assert out.count("many.txt:") == 5
    assert "只显示前 5 条" in out


def test_out_of_range_limits_are_clamped_not_rejected():
    from avid.agent.tools.search import HARD_RESULT_CAP, _limit

    assert _limit(10_000) == HARD_RESULT_CAP
    assert _limit(0) == 1
    assert _limit("不是数字") == MAX_RESULTS


def test_a_long_line_is_truncated(tmp_path):
    (tmp_path / "long.txt").write_text("命中" + "字" * 5000 + "\n", encoding="utf-8")

    out = run("命中", tmp_path)

    assert len(out.splitlines()[0]) < 400


def test_searching_one_file_directly(project):
    out = run("handler", project, path="src/app.py")

    assert "src/app.py:1" in out


def test_the_python_fallback_gives_the_same_shape(project, monkeypatch):
    """Same shape without rg: whether ripgrep is installed must not change what the model sees."""
    monkeypatch.setattr(search_module.shutil, "which", lambda name: None)

    out = run("会话存储", project)

    assert "src/app.py:4: # TODO: 会话存储" in out
    assert "node_modules" not in out and ".git" not in out


def test_the_tool_is_concurrency_safe():
    from avid.agent.tools import specs

    spec = next(item for item in specs() if item.name == "grep_search")
    assert spec.concurrency == "safe" and spec.writes is False


def test_the_reported_limit_constants_are_sane(project):
    assert 0 < MAX_RESULTS < 1000
    assert run("", project).startswith("错误：缺少参数 pattern")
