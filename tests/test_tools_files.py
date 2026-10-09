import pytest

from avid.agent.tools import files, workspace
from avid.agent.tools.files import edit_file, glob_files, read_file, write_file


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Points the workspace root at a temp dir; tests do not depend on repository content."""
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


# ---- read_file ----


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


def test_read_file_without_a_run_spec_reads_outside(sandbox):
    """An outside-workspace read needs no authorization and works without a run spec."""
    outside = sandbox.parent / "outside-read.txt"
    outside.write_text("外面\n", encoding="utf-8")
    try:
        assert read_file({"path": "../outside-read.txt"}) == "外面"
    finally:
        outside.unlink(missing_ok=True)


def test_read_file_reads_outside_the_workspace_with_a_run_spec(sandbox):
    """The boundary is a sandbox capability, not the workspace: reading outside it is allowed."""
    from avid.agent.state import RunState

    outside = sandbox.parent / "outside-read.txt"
    outside.write_text("外面\n", encoding="utf-8")
    state = RunState.for_run(workspace_root=str(sandbox), audit_enabled=False)

    try:
        assert read_file({"path": str(outside)}, state=state) == "外面"
    finally:
        outside.unlink(missing_ok=True)


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


# ---- write_file ----


def test_write_file_creates_file_and_parents(sandbox):
    result = write_file({"path": "sub/dir/a.txt", "content": "你好\n"})

    assert (sandbox / "sub/dir/a.txt").read_text(encoding="utf-8") == "你好\n"
    assert "已新建" in result


def test_write_file_overwrites_existing(sandbox):
    (sandbox / "a.txt").write_text("旧", encoding="utf-8")

    assert "已覆盖" in write_file({"path": "a.txt", "content": "新"})


def test_write_file_writes_outside_the_workspace(sandbox):
    """Outside writes pass straight through: the tool layer does not block them; the sandbox
    mounts them via an automatic grant recorded in the engine ledger."""
    target = sandbox.parent / "x.txt"
    try:
        result = write_file({"path": "../x.txt", "content": "x"})

        assert "已新建" in result
        assert target.read_text(encoding="utf-8") == "x"
    finally:
        target.unlink(missing_ok=True)


def test_full_run_can_write_outside_workspace(sandbox):
    """A full run (complete access) writes outside the workspace; outside writes do not
    require full."""
    from avid.agent.state import RunState

    target = sandbox.parent / "outside-full-write.txt"
    state = RunState.for_run(
        full=True,
        workspace_root=str(sandbox),
        audit_enabled=False,
    )
    try:
        result = write_file({"path": str(target), "content": "full\n"}, state=state)
        assert "已新建" in result
        assert target.read_text(encoding="utf-8") == "full\n"
    finally:
        target.unlink(missing_ok=True)


def test_write_file_still_refuses_protected_host_resources(sandbox):
    """Outside writes are not unguarded: credential paths are the only hard refusal and no
    confirmation overrides it."""
    protected = sandbox.parent / ".ssh" / "id_rsa"

    result = write_file({"path": str(protected), "content": "x"})

    assert "受保护的宿主资源" in result
    assert not protected.exists()


# ---- edit_file ----


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


# ---- checkpoint before writing ----


class RecordingCheckpoint:
    """Checkpoint-sink stand-in: records snapshotted paths and can inject a failure result."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def snapshot(self, path):
        self.calls.append(path)
        return self.result


def _state_with_checkpoint(sandbox, checkpoint):
    from avid.agent.state import RunState

    state = RunState.for_run(workspace_root=str(sandbox), audit_enabled=False)
    state.checkpoint = checkpoint
    return state


def test_write_file_snapshots_before_writing(sandbox):
    checkpoint = RecordingCheckpoint()
    state = _state_with_checkpoint(sandbox, checkpoint)

    write_file({"path": "a.txt", "content": "新"}, state=state)

    assert checkpoint.calls == [sandbox / "a.txt"]
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "新"


def test_write_file_refuses_to_write_when_snapshot_fails(sandbox):
    """A failed snapshot must refuse the write: no change without a snapshot may land."""
    (sandbox / "a.txt").write_text("旧", encoding="utf-8")
    state = _state_with_checkpoint(
        sandbox, RecordingCheckpoint(result="错误：快照失败")
    )

    result = write_file({"path": "a.txt", "content": "新"}, state=state)

    assert result == "错误：快照失败"
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "旧"


def test_edit_file_snapshots_before_writing(sandbox):
    (sandbox / "a.txt").write_text("alpha beta", encoding="utf-8")
    checkpoint = RecordingCheckpoint()
    state = _state_with_checkpoint(sandbox, checkpoint)

    edit_file({"path": "a.txt", "old_string": "beta", "new_string": "B"}, state=state)

    assert checkpoint.calls == [sandbox / "a.txt"]
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "alpha B"


def test_edit_file_refuses_to_write_when_snapshot_fails(sandbox):
    (sandbox / "a.txt").write_text("alpha beta", encoding="utf-8")
    state = _state_with_checkpoint(
        sandbox, RecordingCheckpoint(result="错误：快照失败")
    )

    result = edit_file(
        {"path": "a.txt", "old_string": "beta", "new_string": "B"}, state=state
    )

    assert result == "错误：快照失败"
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "alpha beta"


def test_snapshot_happens_after_validation(sandbox):
    """The snapshot point sits after every validation: a rejected write must not reach disk
    or the checkpoint sink."""
    checkpoint = RecordingCheckpoint()
    state = _state_with_checkpoint(sandbox, checkpoint)

    write_file({"path": "a.txt", "content": None}, state=state)
    edit_file({"path": "a.txt", "old_string": "x", "new_string": "y"}, state=state)

    assert checkpoint.calls == []


# ---- glob ----


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


def test_read_file_does_not_load_the_whole_file(sandbox, monkeypatch):
    """read_file must stream: Path.read_text is made to raise, so the whole-file path cannot
    come back."""
    from pathlib import Path

    big = sandbox / "big.txt"
    big.write_text("\n".join(f"第 {index} 行" for index in range(1, 5001)), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("read_file 不该用 read_text 全量读")

    monkeypatch.setattr(Path, "read_text", forbidden)
    result = read_file({"path": "big.txt", "offset": 10, "limit": 3})

    assert result.splitlines()[0] == "第 10 行"
    assert "截断" in result and "共 5000 行" in result


def test_read_file_offset_beyond_the_end_reports_the_total(sandbox):
    target = sandbox / "small.txt"
    target.write_text("a\nb\nc", encoding="utf-8")  # no trailing newline

    assert "共 3 行" in read_file({"path": "small.txt", "offset": 9})


def test_read_file_window_to_the_end_has_no_truncation_notice(sandbox):
    target = sandbox / "tail.txt"
    target.write_text("a\nb\n", encoding="utf-8")

    assert read_file({"path": "tail.txt", "offset": 1, "limit": 10}) == "a\nb"
    assert read_file({"path": "tail.txt", "offset": 2}) == "b"


def test_read_file_of_an_empty_file_is_empty(sandbox):
    (sandbox / "empty.txt").write_text("", encoding="utf-8")

    assert read_file({"path": "empty.txt"}) == ""
