"""审计：只追加的 JSONL、按天分文件、内联凭据打码、写失败不改结论。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avid.policy.audit import AuditLog, default_audit_dir, redact_inline


def log(directory: Path, *, clock=lambda: 1_700_000_000.0, **kwargs) -> AuditLog:
    return AuditLog(
        directory=directory,
        run_tag="tag1",
        run_id="run_1",
        mode="auto",
        axes={"approval": "classifier", "sandbox": "workspace", "network": "restricted"},
        sandbox={"backend": "bwrap", "enforced": True},
        clock=clock,
        **kwargs,
    )


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- 落盘


def test_each_write_is_one_appended_json_line(tmp_path):
    entry = log(tmp_path)

    entry.write("decision", tool="bash", command="ls", verdict="allow")
    entry.write("decision", tool="bash", command="sudo ls", verdict="deny", decision_kind="danger")

    path = tmp_path / "audit-2023-11-14.jsonl"
    records = read(path)
    assert len(records) == 2
    assert records[0]["kind"] == "decision" and records[0]["tool"] == "bash"
    assert records[0]["run"] == "run_1" and records[0]["mode"] == "auto"
    assert records[0]["axes"]["sandbox"] == "workspace"
    assert records[0]["sandbox"]["enforced"] is True
    assert records[1]["decision_kind"] == "danger"
    assert entry.written == 2 and entry.failures == 0


def test_files_rotate_by_day(tmp_path):
    entry = log(tmp_path)
    day_one = 1_700_000_000.0
    day_two = day_one + 86_400
    entry.clock = lambda: day_one
    entry.write("decision", tool="read_file")
    entry.clock = lambda: day_two
    entry.write("decision", tool="read_file")

    files = sorted(path.name for path in tmp_path.glob("audit-*.jsonl"))
    assert len(files) == 2
    assert all(len(read(tmp_path / name)) == 1 for name in files)


def test_disabled_log_returns_the_record_without_touching_disk(tmp_path):
    entry = log(None)
    record = entry.write("decision", tool="bash")
    assert record is not None and record["tool"] == "bash"
    assert entry.path() is None
    # byok/ 是 conftest model_env 种的模型配置，与审计无关
    assert [p for p in tmp_path.iterdir() if p.name != "byok"] == []


def test_run_tag_is_the_fallback_identity(tmp_path):
    entry = AuditLog(directory=tmp_path, run_tag="tag-only", clock=lambda: 1_700_000_000.0)
    entry.write("decision")
    assert read(tmp_path / "audit-2023-11-14.jsonl")[0]["run"] == "tag-only"


# ---------------------------------------------------------------- 失败不改结论


def test_write_failure_is_counted_and_never_raised(tmp_path):
    """审计失败是"少了一条记录"，不是"这次调用该失败"。"""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    entry = log(blocked / "audit")

    record = entry.write("decision", tool="bash")

    assert record is not None and record["tool"] == "bash"
    assert entry.failures == 1 and entry.written == 0
    assert entry.summary()["failures"] == 1


def test_summary_reports_the_current_path(tmp_path):
    entry = log(tmp_path)
    entry.write("run_start")
    summary = entry.summary()
    assert summary["path"].endswith("audit-2023-11-14.jsonl")
    assert summary["written"] == 1 and summary["failures"] == 0


# ---------------------------------------------------------------- 打码与截断


def test_inline_credentials_are_redacted_but_the_command_is_kept(tmp_path):
    entry = log(tmp_path)
    entry.write(
        "decision",
        command='curl -H "Authorization: Bearer sk-live-abc" https://api.example.com',
        other="token=topsecret",
    )
    record = read(tmp_path / "audit-2023-11-14.jsonl")[0]
    assert "sk-live-abc" not in json.dumps(record)
    assert "topsecret" not in json.dumps(record)
    # 命令结构留下：审计要能回答"它想干什么"
    assert "curl" in record["command"] and "api.example.com" in record["command"]
    assert "***" in record["command"]


def test_redact_handles_bearer_without_a_keyword():
    assert "sk-1" not in redact_inline("curl -H 'Bearer sk-1' x")


def test_long_fields_are_truncated(tmp_path):
    entry = log(tmp_path)
    entry.write("decision", command="x" * 5000)
    record = read(tmp_path / "audit-2023-11-14.jsonl")[0]
    assert len(record["command"]) < 2100
    assert record["command"].endswith("…")


def test_non_ascii_is_written_as_utf8(tmp_path):
    entry = log(tmp_path)
    entry.write("decision", reason="目标在工作区之外")
    raw = (tmp_path / "audit-2023-11-14.jsonl").read_text(encoding="utf-8")
    assert "目标在工作区之外" in raw  # 不是 \uXXXX 转义
    assert read(tmp_path / "audit-2023-11-14.jsonl")[0]["reason"] == "目标在工作区之外"


def test_nested_values_are_cleaned(tmp_path):
    entry = log(tmp_path)
    entry.write("decision", targets=["/etc/hostname", {"k": "token=abc"}])
    record = read(tmp_path / "audit-2023-11-14.jsonl")[0]
    assert record["targets"][0] == "/etc/hostname"
    assert record["targets"][1]["k"] == "token=***"
    assert entry.failures == 0


# ---------------------------------------------------------------- 目录优先级


def test_audit_dir_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("AVID_AUDIT_DIR", raising=False)
    monkeypatch.delenv("AVID_HOME", raising=False)
    home = tmp_path / "home"
    assert default_audit_dir(home) == home / ".avid" / "audit"

    monkeypatch.setenv("AVID_HOME", str(tmp_path / "avidhome"))
    assert default_audit_dir(home) == tmp_path / "avidhome" / "audit"

    monkeypatch.setenv("AVID_AUDIT_DIR", str(tmp_path / "explicit"))
    assert default_audit_dir(home) == tmp_path / "explicit"


@pytest.mark.parametrize("kind", ["run_start", "decision", "full_grant"])
def test_kind_is_free_form_so_new_record_types_need_no_migration(tmp_path, kind):
    entry = log(tmp_path)
    entry.write(kind)
    assert read(tmp_path / "audit-2023-11-14.jsonl")[0]["kind"] == kind
