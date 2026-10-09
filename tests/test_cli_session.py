"""CLI session flags: create, continue, list, delete.

End to end through the real CLI with only the model swapped for a FakeChat: loop, sessions
and file writes are real, the workspace root becomes tmp_path, and sessions land under
AVID_HOME's sessions/<workspace id>/.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from support import make_turn as scripted_turn
from support import tool_call

from avid import cli
from avid.agent.context import TAIL_HEADER
from avid.agent.run import Run as RealRun
from avid.agent.stop import STOP_FINAL_TEXT, RunOutcome
from avid.agent.tools import workspace
from avid.providers.client import Turn, Usage
from avid.security import sandbox as sandbox_module
from avid.security.sandbox import BACKEND_BWRAP, BackendProbe


class FakeChat:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.turns[len(self.requests) - 1]


def make_turn(text="回答"):
    return Turn(
        message={"role": "assistant", "content": text},
        text=text,
        tool_calls=[],
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="m",
        finish_reason="stop",
    )


class Model:
    """Install a fake model answering fixed texts (spec.chat swapped, the real Run stays)."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch

    def answer(self, *texts):
        chat = FakeChat(*[make_turn(text) for text in texts])

        class FakeRun:
            def __init__(self, messages, spec, **kwargs):
                self._args = (messages, replace(spec, chat=chat), kwargs)

            def run(self):
                messages, spec, kwargs = self._args
                return RealRun(messages, spec, **kwargs).run()

        self.monkeypatch.setattr(cli, "Run", FakeRun)
        return chat


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path, hook_registry) -> Path:
    # conftest's model_env seeds the model config
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def model(monkeypatch) -> Model:
    return Model(monkeypatch)


def session_dir(root: Path) -> Path:
    """The session directory: <store>/<workspace id>, not inside the workspace."""
    from avid.services.workspace_registry import sessions_root
    from avid.services.workspaces import bound_workspace

    return sessions_root(bound_workspace(root))


def session_files(root: Path) -> list[Path]:
    return sorted(session_dir(root).glob("*.jsonl"))


def session_id(stderr: str) -> str:
    match = re.search(r"session=(\S+)", stderr)
    assert match, stderr
    return match.group(1)


def entries_of(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        payload = json.loads(line)
        for record in payload if isinstance(payload, list) else [payload]:
            if record["kind"] == "entry":
                records.append(record)
    return records


# ---- create and continue ----


def test_new_session_runs_and_records(sandbox, model, capsys):
    model.answer("回答")
    assert cli.main(["--agent", "--new-session", "第一问"]) == 0

    out, err = capsys.readouterr()
    assert out.strip() == "回答"
    assert "新建" in err

    files = session_files(sandbox)
    assert len(files) == 1
    assert files[0].name.endswith(f"_{session_id(err)}.jsonl")
    records = entries_of(files[0])
    assert [record["message"]["role"] for record in records] == ["user", "assistant"]
    assert records[0]["message"]["content"] == "第一问"


def test_existing_session_continues_with_history(sandbox, model, capsys):
    model.answer("第一答")
    cli.main(["--agent", "--new-session", "第一问"])
    created = session_id(capsys.readouterr().err)

    chat = model.answer("第二答")
    assert cli.main(["--agent", "--session", created, "第二问"]) == 0
    err = capsys.readouterr().err
    assert "续接" in err

    visible = [
        message["content"]
        for message in chat.requests[0]["messages"]
        if not str(message.get("content", "")).startswith(TAIL_HEADER)
    ]
    assert visible == [
        "第一问",
        "第一答",
        "第二问",
    ]
    records = entries_of(session_files(sandbox)[0])
    assert [record["message"]["role"] for record in records] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_unknown_session_id_is_created_on_demand(sandbox, model, capsys):
    model.answer("答")
    assert cli.main(["--agent", "--session", "my-id", "问"]) == 0
    err = capsys.readouterr().err
    assert "新建" in err
    assert session_files(sandbox)[0].name.endswith("_my-id.jsonl")


def test_session_flag_implies_agent_mode(sandbox, model, capsys):
    model.answer("答")
    assert cli.main(["--session", "s", "问"]) == 0
    capsys.readouterr()
    assert len(session_files(sandbox)) == 1


def test_plain_agent_run_does_not_create_sessions(sandbox, model, capsys):
    model.answer("答")
    assert cli.main(["--agent", "问"]) == 0
    capsys.readouterr()
    assert not session_dir(sandbox).exists()


# ---- list and delete ----


def test_list_sessions_shows_name_and_count(sandbox, model, capsys):
    model.answer("答")
    cli.main(["--agent", "--new-session", "--session-name", "演示会话", "问"])
    created = session_id(capsys.readouterr().err)

    assert cli.main(["--list-sessions"]) == 0
    out = capsys.readouterr().out
    row = out.strip().splitlines()[0].split("\t")
    assert row[0] == created
    assert row[2] == "2"
    # The list has a workspace column, so the name is one field later
    assert row[3].startswith("w-")
    assert row[4] == "演示会话"


def test_list_sessions_when_empty(sandbox, capsys):
    assert cli.main(["--list-sessions"]) == 0
    assert "还没有会话" in capsys.readouterr().out


def test_delete_session_removes_the_file(sandbox, model, capsys):
    model.answer("答")
    cli.main(["--agent", "--new-session", "问"])
    created = session_id(capsys.readouterr().err)

    assert cli.main(["--delete-session", created]) == 0
    assert "已删除会话" in capsys.readouterr().err
    assert session_files(sandbox) == []

    assert cli.main(["--delete-session", created]) == 1
    assert "没有这个会话" in capsys.readouterr().err


# ---- argument validation ----


@pytest.mark.parametrize(
    "argv",
    [
        ["--session", "a", "--new-session", "问"],
        ["--session-name", "名字", "--agent", "问"],
        # No --permission flag: full access comes only from --allow-full-access
        ["--agent", "--permission", "auto", "问"],
    ],
)
def test_argument_errors_exit_2(sandbox, argv):
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2


def test_no_prompt_enters_the_interactive_session(sandbox, monkeypatch):
    """No arguments enters the interactive session (continuing the most recent one); EOF exits
    with status 0."""

    def eof(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", eof)

    assert cli.main(["--new-session"]) == 0
    assert cli.main([]) == 0


# ---- workspaces ----


def test_new_session_records_the_selected_workspace(sandbox, model, capsys, tmp_path):
    other = tmp_path.parent / f"other-{tmp_path.name}"
    other.mkdir()
    model.answer("答")

    assert cli.main(["--agent", "--new-session", "--workspace", str(other), "问"]) == 0

    err = capsys.readouterr().err
    assert "工作区 w-" in err
    assert str(other) in err
    assert session_files(sandbox) == []  # the session lands in the selected workspace
    assert len(session_files(other)) == 1


def test_session_list_shows_the_workspace_column(sandbox, model, capsys):
    model.answer("答")
    cli.main(["--agent", "--new-session", "问"])
    capsys.readouterr()

    assert cli.main(["--list-sessions"]) == 0

    row = capsys.readouterr().out.strip().splitlines()[0].split("\t")
    assert row[3].startswith("w-")


def test_run_state_carries_the_workspace_and_the_default_permission(sandbox, model, monkeypatch, capsys):
    """With no flag the run is normal and still requests an enforced workspace sandbox."""
    # The backend is a host fact (CI runners rarely have bwrap): inject a working probe so
    # the assertion pins the CLI's request, not the machine.
    probe = BackendProbe(
        backend=BACKEND_BWRAP,
        binary="/usr/bin/bwrap",
        available=True,
        network_isolation=True,
        reason=None,
        landlock=3,
    )
    monkeypatch.setattr(sandbox_module, "probe_backend", lambda: probe)

    registry = cli.WorkspaceRegistry()
    ws = registry.add(sandbox)
    seen = {}

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            seen.update(kwargs)

        def run(self):
            return RunOutcome(text="答", reason=STOP_FINAL_TEXT)

    monkeypatch.setattr(cli, "Run", FakeRun)

    assert cli.main(["--agent", "--new-session", "问"]) == 0

    # Permission and workspace root live in the run spec (state.security), so the assertions
    # read the state actually handed to the loop.
    state = seen.get("state")
    assert state.permission_mode == "normal"
    assert state.workspace_root == ws.root
    assert state.security.full is False
    assert state.security.sandbox.policy == "workspace"
    assert state.security.sandbox.enforced is True
    capsys.readouterr()


def test_allow_full_access_turns_on_full_permission(sandbox, model, monkeypatch, capsys):
    """--allow-full-access is the only switch for full access: it skips destructive
    confirmation and disables the sandbox."""
    seen = {}

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            seen.update(kwargs)

        def run(self):
            return RunOutcome(text="答", reason=STOP_FINAL_TEXT)

    monkeypatch.setattr(cli, "Run", FakeRun)

    assert cli.main(["--agent", "--new-session", "--allow-full-access", "问"]) == 0

    state = seen.get("state")
    assert state.permission_mode == "full"
    assert state.security.full is True
    assert state.security.sandbox.policy == "disabled"
    capsys.readouterr()


def test_workspace_subcommand_add_list_remove(sandbox, capsys, tmp_path):
    other = tmp_path.parent / f"ws-{tmp_path.name}"
    other.mkdir()

    assert cli.main(["workspace", "add", str(other), "--name", "另一个"]) == 0
    added = capsys.readouterr().out.strip().split("\t")
    assert added[1] == str(other.resolve())
    assert added[2] == "另一个"

    assert cli.main(["workspace", "list"]) == 0
    assert "另一个" in capsys.readouterr().out

    # The `workspace permission` subcommand is gone: argparse rejects it
    with pytest.raises(SystemExit) as info:
        cli.main(["workspace", "permission", added[0], "auto"])
    assert info.value.code == 2

    assert cli.main(["workspace", "remove", added[0]]) == 0
    # Removing leaves a tombstone: not a candidate, but sessions and entries stay
    assert "会话与磁盘数据都留着" in capsys.readouterr().out
    assert cli.main(["workspace", "list"]) == 0
    assert "另一个" not in capsys.readouterr().out
    assert cli.main(["workspace", "add", str(other), "--name", "另一个"]) == 0
    assert cli.main(["workspace", "list"]) == 0
    assert "另一个" in capsys.readouterr().out


def test_workspace_subcommand_reports_unknown(monkeypatch, capsys, tmp_path):
    assert cli.main(["workspace", "add", str(tmp_path / "missing")]) == 1

    assert "工作区错误" in capsys.readouterr().err


def test_cli_never_writes_the_registry(sandbox, model, capsys, tmp_path):
    """Reading and running write no registry (only `avid workspace add` does), so the file lists
    what you registered, not what you ran."""
    registry_file = tmp_path / "avid-home" / "workspaces.json"
    model.answer("答")

    assert cli.main(["--agent", "--new-session", "问"]) == 0
    capsys.readouterr()
    assert cli.main(["--list-sessions"]) == 0
    capsys.readouterr()

    assert not registry_file.exists()

    assert cli.main(["workspace", "add", str(sandbox)]) == 0
    assert registry_file.exists()


def test_workspace_add_reports_a_duplicate_without_adding_twice(sandbox, capsys, tmp_path):
    other = tmp_path.parent / f"dup-{tmp_path.name}"
    other.mkdir()

    assert cli.main(["workspace", "add", str(other)]) == 0
    capsys.readouterr()
    assert cli.main(["workspace", "add", str(other)]) == 0
    assert "已登记过" in capsys.readouterr().out

    assert cli.main(["workspace", "list"]) == 0
    assert capsys.readouterr().out.count(str(other.resolve())) == 1


def test_interactive_turn_skill_and_unknown_command(sandbox, model, monkeypatch, capsys, tmp_path):
    """Plain input runs once; a /skill writes its full text into the session; an unknown
    command only prints the hint and calls no model."""
    model.answer("答")
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text(
        "---\ndescription: 演示技能\n---\n这是演示技能的正文", encoding="utf-8"
    )

    answers = iter(["你好", "/demo", "/nope", "/compact"])

    def fake_input(prompt=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError() from None

    monkeypatch.setattr("builtins.input", fake_input)

    assert cli.main(["--new-session"]) == 0

    out, err = capsys.readouterr()
    assert "答" in out
    assert "已载入技能 demo" in err
    assert "/compact" in err and "/demo" in err   # the hint lists commands and skills
    assert "没有可压缩的更早历史" in err

    # The skill text really lands in the session (recorded, resumable)
    from avid.services.workspace_registry import sessions_root
    from avid.session import JsonlSessionRepo, messages_for_branch

    target = cli._resolve_workspace(None)
    repo = JsonlSessionRepo(sessions_root(target), workspace=target.id)
    try:
        newest = repo.list()[0]
        session = repo.open(newest)
        try:
            contents = [m["content"] for m in messages_for_branch(session)]
            assert any("这是演示技能的正文" in str(c) for c in contents)
        finally:
            session.close()
    finally:
        repo.close()


def test_interactive_rewind_restores_files_and_moves_the_tip_back(
    sandbox, monkeypatch, capsys
):
    """/rewind restores files to before that input and drops the following turns from the
    conversation; orphans stay on disk.
    """
    # Model.answer only wraps plain-text turns; a real write_file needs FakeRun wired by hand
    chat = FakeChat(
        scripted_turn("", [tool_call("write_file", '{"path": "notes.txt", "content": "第一版"}')]),
        scripted_turn("第一答"),
        scripted_turn(
            "",
            [tool_call("write_file", '{"path": "notes.txt", "content": "第二版"}', "call_2")],
        ),
        scripted_turn("第二答"),
    )

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            self._args = (messages, replace(spec, chat=chat), kwargs)

        def run(self):
            messages, spec, kwargs = self._args
            return RealRun(messages, spec, **kwargs).run()

    monkeypatch.setattr(cli, "Run", FakeRun)

    answers = iter(["写一下", "改成第二版", "/rewind"])

    def fake_input(prompt=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError() from None

    monkeypatch.setattr("builtins.input", fake_input)

    assert cli.main(["--new-session"]) == 0

    err = capsys.readouterr().err
    assert "已回滚" in err and "移出 4 条" in err and "恢复 1 个" in err
    assert (sandbox / "notes.txt").read_text(encoding="utf-8") == "第一版"

    from avid.services.workspace_registry import sessions_root
    from avid.session import JsonlSessionRepo, messages_for_branch

    target = cli._resolve_workspace(None)
    repo = JsonlSessionRepo(sessions_root(target), workspace=target.id)
    try:
        session = repo.open(repo.list()[0])
        try:
            contents = [str(m.get("content")) for m in messages_for_branch(session)]
            assert contents[0] == "写一下" and contents[-1] == "第一答"
            assert "改成第二版" not in contents and "第二答" not in contents
        finally:
            session.close()
    finally:
        repo.close()



# ---- session store and legacy migration ----


def legacy_session_file(root: Path, session_id: str = "s-legacy") -> Path:
    """Create a real session in the legacy layout (<workspace root>/.avid/sessions)."""
    from avid.services.workspace_registry import derive_id
    from avid.session import JsonlSessionRepo

    repo = JsonlSessionRepo(root / ".avid" / "sessions", workspace=derive_id(root))
    try:
        repo.create(id=session_id)
    finally:
        repo.close()
    return next((root / ".avid" / "sessions").glob(f"*_{session_id}.jsonl"))


def test_session_dir_reports_the_store_and_its_source(sandbox, capsys):
    from avid.security import userdirs

    assert cli.main(["session", "dir"]) == 0

    out = capsys.readouterr().out
    assert str(userdirs.sessions_dir()) in out
    assert "默认位置" in out


def test_session_dir_hints_at_sessions_left_in_the_old_layout(sandbox, capsys):
    legacy_session_file(sandbox)

    assert cli.main(["session", "dir"]) == 0

    assert "旧位置" in capsys.readouterr().err


def test_session_migrate_moves_them_and_listing_still_finds_them(sandbox, capsys):
    """After the move they are still listed by id: migrating must not lose them."""
    legacy_session_file(sandbox)

    assert cli.main(["session", "migrate", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "已搬 1 个" in out and "清掉 1 个空的旧目录" in out
    assert not (sandbox / ".avid" / "sessions").exists()
    assert len(session_files(sandbox)) == 1

    assert cli.main(["--list-sessions"]) == 0
    assert "s-legacy" in capsys.readouterr().out


def test_session_migrate_asks_before_touching_anything(sandbox, capsys, monkeypatch):
    file = legacy_session_file(sandbox)
    prompts: list[str] = []

    def answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", answer)

    assert cli.main(["session", "migrate"]) == 1

    assert file.exists()
    assert any("现在搬？" in prompt for prompt in prompts)
    assert "没搬" in capsys.readouterr().err


def test_session_migrate_with_nothing_to_do_is_not_an_error(sandbox, capsys):
    assert cli.main(["session", "migrate", "--yes"]) == 0
    assert "没有可搬的会话" in capsys.readouterr().out


def test_session_migrate_takes_a_store_root_through_from(sandbox, capsys, tmp_path):
    """--from names an old central store: its workspace id subdirectory carries ownership."""
    from avid.services.workspace_registry import derive_id
    from avid.session import JsonlSessionRepo

    old = tmp_path / "old-store"
    owner = derive_id(sandbox)
    repo = JsonlSessionRepo(old / owner, workspace=None)
    try:
        repo.create(id="s-old-store")
    finally:
        repo.close()

    assert cli.main(["session", "migrate", "--from", str(old), "--yes"]) == 0

    assert "已搬 1 个" in capsys.readouterr().out
    assert len(session_files(sandbox)) == 1


def test_session_migrate_finds_the_previous_store_through_the_index(sandbox, capsys, tmp_path):
    """After the store moves only the index remembers the old location, and migrate must
    follow that lead back.
    """
    from avid.index import db as index_db
    from avid.index.writer import indexed_store_root, record_store_root

    legacy_session_file(sandbox)  # a session exists in the legacy layout under cwd
    legacy = str(sandbox / ".avid" / "sessions")
    conn = index_db.open_db()
    try:
        record_store_root(conn, sandbox / ".avid" / "sessions")
        assert indexed_store_root(conn) == legacy
    finally:
        conn.close()

    # Point the current store elsewhere; the legacy layout under cwd stays
    target = tmp_path / "moved-sessions"
    monkeypatch_env = {"AVID_SESSIONS_DIR": str(target)}

    import os

    saved = {name: os.environ.get(name) for name in monkeypatch_env}
    os.environ.update(monkeypatch_env)
    try:
        assert cli.main(["session", "migrate", "--yes"]) == 0
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    out = capsys.readouterr()
    assert "已搬 1 个" in out.out
    moved = list(target.rglob("*.jsonl"))
    assert len(moved) == 1
    assert "索引记着的旧会话目录" in out.err
