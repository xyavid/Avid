import pytest

from avid.tools import files, workspace
from avid.tools.files import edit_file, glob_files, read_file, write_file


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把工作区根指向临时目录，测试不依赖仓库真实内容。"""
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


# ---------- read_file ----------


def test_read_file_returns_content(sandbox):
    (sandbox / "a.txt").write_text("第一行\n第二行\n", encoding="utf-8")

    assert read_file({"path": "a.txt"}) == "第一行\n第二行"


def test_read_file_pages_with_offset_and_limit(sandbox):
    (sandbox / "a.txt").write_text(
        "".join(f"L{i}\n" for i in range(1, 11)), encoding="utf-8"
    )

    result = read_file({"path": "a.txt", "offset": 3, "limit": 2})

    assert result.startswith("L3\nL4")
    assert "共 10 行" in result


def test_read_file_accepts_absolute_path_inside_workspace(sandbox):
    target = sandbox / "a.txt"
    target.write_text("内容", encoding="utf-8")

    assert read_file({"path": str(target)}) == "内容"


def test_read_file_refuses_escape(sandbox):
    assert "工作区外" in read_file({"path": "../outside.txt"})


def test_read_file_reports_missing_file(sandbox):
    assert "文件不存在" in read_file({"path": "nope.txt"})


def test_read_file_reports_directory(sandbox):
    (sandbox / "d").mkdir()

    assert "是目录" in read_file({"path": "d"})


def test_read_file_reports_empty_path(sandbox):
    assert read_file({"path": "  "}).startswith("错误：")


def test_read_file_reports_offset_past_end(sandbox):
    (sandbox / "a.txt").write_text("only\n", encoding="utf-8")

    assert "超出文件范围" in read_file({"path": "a.txt", "offset": 5})


def test_read_file_truncates_long_content(sandbox, monkeypatch):
    monkeypatch.setattr(files, "MAX_READ_CHARS", 10)
    (sandbox / "a.txt").write_text("x" * 100, encoding="utf-8")

    assert "已按字符数截断" in read_file({"path": "a.txt"})


# ---------- write_file ----------


def test_write_file_creates_file_and_parents(sandbox):
    result = write_file({"path": "sub/dir/a.txt", "content": "你好\n"})

    assert (sandbox / "sub/dir/a.txt").read_text(encoding="utf-8") == "你好\n"
    assert "已新建" in result


def test_write_file_overwrites_existing(sandbox):
    (sandbox / "a.txt").write_text("旧", encoding="utf-8")

    assert "已覆盖" in write_file({"path": "a.txt", "content": "新"})


def test_write_file_refuses_escape(sandbox):
    result = write_file({"path": "../x.txt", "content": "x"})

    assert "工作区外" in result
    assert not (sandbox.parent / "x.txt").exists()


def test_write_file_requires_string_content(sandbox):
    assert "content" in write_file({"path": "a.txt"})


# ---------- edit_file ----------


def test_edit_file_replaces_single_occurrence(sandbox):
    (sandbox / "a.txt").write_text("alpha beta gamma", encoding="utf-8")

    assert "已替换" in edit_file(
        {"path": "a.txt", "old_string": "beta", "new_string": "B"}
    )
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "alpha B gamma"


def test_edit_file_refuses_when_old_string_missing(sandbox):
    (sandbox / "a.txt").write_text("alpha", encoding="utf-8")

    result = edit_file({"path": "a.txt", "old_string": "zzz", "new_string": "x"})

    assert "不存在" in result
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "alpha"


def test_edit_file_refuses_when_old_string_is_ambiguous(sandbox):
    (sandbox / "a.txt").write_text("x\nx\n", encoding="utf-8")

    result = edit_file({"path": "a.txt", "old_string": "x", "new_string": "y"})

    assert "不唯一" in result
    assert "2" in result
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "x\nx\n"


def test_edit_file_can_delete_text(sandbox):
    (sandbox / "a.txt").write_text("keep\ndrop\n", encoding="utf-8")

    edit_file({"path": "a.txt", "old_string": "drop\n", "new_string": ""})

    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "keep\n"


def test_edit_file_requires_old_string(sandbox):
    assert "old_string" in edit_file({"path": "a.txt", "new_string": "x"})


# ---------- glob ----------


def test_glob_finds_files_by_pattern(sandbox):
    (sandbox / "src").mkdir()
    (sandbox / "src" / "a.py").write_text("", encoding="utf-8")
    (sandbox / "src" / "b.txt").write_text("", encoding="utf-8")

    assert glob_files({"pattern": "**/*.py"}) == "src/a.py"


def test_glob_reports_no_match(sandbox):
    assert "未找到" in glob_files({"pattern": "**/*.rs"})


def test_glob_scopes_to_subdirectory(sandbox):
    (sandbox / "src").mkdir()
    (sandbox / "src" / "a.py").write_text("", encoding="utf-8")
    (sandbox / "b.py").write_text("", encoding="utf-8")

    assert glob_files({"pattern": "*.py", "path": "src"}) == "src/a.py"


def test_glob_requires_pattern(sandbox):
    assert "pattern" in glob_files({})
