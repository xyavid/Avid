"""Index check and repair: every way the index can rot, and what check plus --fix do about it.

Failure list (AGENTS.md §6), each with the finding kind and its fix:
  - session file deleted -> missing_file / forget
  - file truncated -> cursor_beyond_eof / rebuild
  - file appended without a notification -> behind / reindex
  - file rewritten at the same length -> touched / rebuild
  - row count disagreeing with the file -> rows_mismatch / rebuild
  - unknown storage version -> status / no fix
  - index built over another session store -> other_store
"""

from __future__ import annotations

import json
import os

from index_cases import ALPHA, append_message, make_session

from avid.index import check as index_check
from avid.index import db as index_db
from avid.index import queries


def findings_by_kind(report) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for finding in report.findings:
        grouped.setdefault(finding.kind, []).append(finding)
    return grouped


def test_a_healthy_index_has_no_findings(indexer, store):
    make_session(store, session_id="s-ok", messages=({"role": "user", "content": "好"},))
    indexer.index_all()

    report = index_check.check_index(indexer, roots=[store])

    assert report.findings == ()
    assert report.sessions == 1
    assert report.store_matches is True


def test_a_deleted_file_is_reported_and_can_be_fixed(indexer, store):
    file = make_session(store, session_id="s-gone", messages=({"role": "user", "content": "删我"},))
    indexer.index_all()
    file.unlink()

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "missing_file" in kinds
    assert kinds["missing_file"][0].session_id == "s-gone"
    assert kinds["missing_file"][0].fix == "forget"

    index_check.apply_fixes(indexer, report)

    assert queries.get_session(indexer.conn, "s-gone") is None


def test_a_truncated_file_is_rebuilt(indexer, store):
    file = make_session(
        store,
        session_id="s-cut",
        messages=({"role": "user", "content": "一"}, {"role": "assistant", "content": "二"}),
    )
    indexer.index_all()
    lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
    file.write_text("".join(lines[:2]), encoding="utf-8")  # header + first commit

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "cursor_beyond_eof" in kinds
    assert kinds["cursor_beyond_eof"][0].fix == "rebuild"

    index_check.apply_fixes(indexer, report)

    row = queries.get_session(indexer.conn, "s-cut")
    assert row is not None and row.entry_count == 1
    assert row.indexed_bytes == file.stat().st_size
    assert index_check.check_index(indexer, roots=[store]).findings == ()


def test_an_append_without_a_notification_shows_up_as_behind(indexer, store):
    """A lost notification or a killed process lands here: check says behind, fix catches up."""
    file = make_session(store, session_id="s-behind", messages=({"role": "user", "content": "一"},))
    indexer.index_all()
    append_message(file, {"role": "assistant", "content": "二"})  # no notify call

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "behind" in kinds and kinds["behind"][0].fix == "reindex"

    index_check.apply_fixes(indexer, report)

    assert queries.get_session(indexer.conn, "s-behind").entry_count == 2


def test_a_rewrite_with_the_same_length_is_caught_by_mtime(indexer, store):
    """Same length but edited content: caught by comparing mtime with the indexed moment."""
    file = make_session(store, session_id="s-touched", messages=({"role": "user", "content": "原话"},))
    indexer.index_all()
    before = file.stat().st_size
    body = file.read_text(encoding="utf-8").replace("原话", "改过")
    assert len(body.encode("utf-8")) == before - (len("原话".encode()) - len("改过".encode()))
    file.write_text(body, encoding="utf-8")
    stat = file.stat()
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "touched" in kinds and kinds["touched"][0].fix == "rebuild"

    index_check.apply_fixes(indexer, report)

    assert queries.search_entries(indexer.conn, "改过")


def test_row_count_disagreeing_with_the_file_is_reported(indexer, store):
    make_session(
        store, session_id="s-rows", messages=({"role": "user", "content": "一"}, {"role": "assistant", "content": "二"})
    )
    indexer.index_all()
    with index_db.transaction(indexer.conn):
        indexer.conn.execute(
            "DELETE FROM entries WHERE entry_id = (SELECT entry_id FROM entries WHERE session_id = ? LIMIT 1)",
            ("s-rows",),
        )

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "rows_mismatch" in kinds and kinds["rows_mismatch"][0].fix == "rebuild"

    index_check.apply_fixes(indexer, report)

    assert len(queries.entries_of(indexer.conn, "s-rows")) == 2


def test_a_file_the_index_never_saw_is_reported(indexer, store):
    indexer.index_all()
    make_session(store, session_id="s-new", messages=({"role": "user", "content": "刚建的"},))

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "unindexed_file" in kinds and kinds["unindexed_file"][0].fix == "reindex"

    index_check.apply_fixes(indexer, report)

    assert queries.get_session(indexer.conn, "s-new") is not None


def test_an_unknown_version_is_reported_without_a_fix(indexer, store):
    file = make_session(store, session_id="s-future", messages=({"role": "user", "content": "未来"},))
    lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
    header = json.loads(lines[0])
    header["storageVersion"] = 99
    file.write_text(json.dumps(header) + "\n" + "".join(lines[1:]), encoding="utf-8")
    indexer.index_all()

    report = index_check.check_index(indexer, roots=[store])

    kinds = findings_by_kind(report)
    assert "status" in kinds
    assert kinds["status"][0].fix is None
    assert "unsupported" in kinds["status"][0].detail


def test_an_index_built_for_another_store_is_flagged(indexer, store, tmp_path):
    make_session(store, session_id="s-elsewhere", messages=({"role": "user", "content": "一"},))
    indexer.index_all()
    assert index_check.check_index(indexer, roots=[store]).store_matches is True

    report = index_check.check_index(indexer, roots=[tmp_path / "another-store"])

    assert report.store_matches is False
    assert any(finding.kind == "other_store" for finding in report.findings)


def test_fix_all_counts_what_it_did(indexer, store):
    file = make_session(store, session_id="s-mix", messages=({"role": "user", "content": "一"},))
    indexer.index_all()
    append_message(file, {"role": "assistant", "content": "二"})
    (store / ALPHA / "gone.jsonl").write_text("{}", encoding="utf-8")

    report = index_check.check_index(indexer, roots=[store])
    tally = index_check.apply_fixes(indexer, report)

    assert tally.reindexed + tally.rebuilt + tally.forgotten + tally.skipped >= 1
    # gone.jsonl is not a session file and stays reported: the index never deletes foreign files
    assert [item.kind for item in index_check.check_index(indexer, roots=[store]).findings] == [
        "unreadable_file"
    ]
