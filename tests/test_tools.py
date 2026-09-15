import pytest

from avid import tools
from avid.tools import TOOLS, TOOL_IMPLS, read_file


def test_reads_a_file_inside_the_workspace():
    assert 'name = "avid"' in read_file({"path": "pyproject.toml"})


def test_accepts_absolute_path_inside_the_workspace():
    absolute = str(tools.WORKSPACE_ROOT / "pyproject.toml")

    assert "avid" in read_file({"path": absolute})


def test_refuses_paths_outside_the_workspace():
    assert "工作区外" in read_file({"path": "/etc/passwd"})


def test_refuses_escape_via_parent_directory():
    assert "工作区外" in read_file({"path": "../../etc/passwd"})


def test_missing_file_is_reported_as_text():
    assert "文件不存在" in read_file({"path": "no/such/file.txt"})


def test_directory_is_reported_as_text():
    assert "是目录" in read_file({"path": "src"})


def test_missing_argument_is_reported():
    assert "缺少参数 path" in read_file({})


def test_long_content_is_truncated(monkeypatch):
    monkeypatch.setattr(tools, "MAX_CHARS", 10)

    result = read_file({"path": "pyproject.toml"})

    assert "已截断" in result
    assert result.startswith('[project]\n')


def test_schemas_and_implementations_stay_in_sync():
    declared = {item["function"]["name"] for item in TOOLS}

    assert declared == set(TOOL_IMPLS)
    assert declared == {"read_file"}
