"""CLI 的会话旗标：新建、续接、列举、销毁。

端到端跑真 CLI：只把模型换成 ``FakeChat``（替换 ``cli.agent_loop`` 注入），
循环、会话、文件落盘都是真的。工作区根目录被换成 tmp_path，会话因此写在
临时目录下的 ``.avid/sessions/``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from avid import cli
from avid.ai.client import Turn, Usage
from avid.runtime import hooks
from avid.runtime.loop import agent_loop as real_agent_loop
from avid.tools import workspace


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
    """按需安装一个返回固定回答的假模型。"""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch

    def answer(self, *texts):
        chat = FakeChat(*[make_turn(text) for text in texts])

        def run(messages, **kwargs):
            return real_agent_loop(messages, chat=chat, **kwargs)

        self.monkeypatch.setattr(cli, "agent_loop", run)
        return chat


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(hooks, "HOOKS", {event: [] for event in hooks.EVENTS})
    monkeypatch.setenv("AVID_API_KEY", "test-key")
    monkeypatch.setenv("AVID_MODEL", "test-model")
    return tmp_path


@pytest.fixture
def model(monkeypatch) -> Model:
    return Model(monkeypatch)


def session_files(root: Path) -> list[Path]:
    return sorted((root / ".avid" / "sessions").glob("*.jsonl"))


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


# ---------------- 新建与续接 ----------------


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

    assert [message["content"] for message in chat.requests[0]["messages"]] == [
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
    assert not (sandbox / ".avid" / "sessions").exists()


# ---------------- 列举与销毁 ----------------


def test_list_sessions_shows_name_and_count(sandbox, model, capsys):
    model.answer("答")
    cli.main(["--agent", "--new-session", "--session-name", "演示会话", "问"])
    created = session_id(capsys.readouterr().err)

    assert cli.main(["--list-sessions"]) == 0
    out = capsys.readouterr().out
    row = out.strip().splitlines()[0].split("\t")
    assert row[0] == created
    assert row[2] == "2"
    # 阶段 18 起列表多一列归属工作区，名字后移一位。
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


# ---------------- 参数校验 ----------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--session", "a", "--new-session", "问"],
        ["--session-name", "名字", "--agent", "问"],
        ["--agent", "--new-session"],
    ],
)
def test_argument_errors_exit_2(sandbox, argv):
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2


# ---------------- 工作区（阶段 18） ----------------


def test_new_session_records_the_selected_workspace(sandbox, model, capsys, tmp_path):
    other = tmp_path.parent / f"other-{tmp_path.name}"
    other.mkdir()
    model.answer("答")

    assert cli.main(["--agent", "--new-session", "--workspace", str(other), "问"]) == 0

    err = capsys.readouterr().err
    assert "工作区 w-" in err
    assert str(other) in err
    assert session_files(sandbox) == []  # 会话落在被选中的工作区里
    assert len(session_files(other)) == 1


def test_session_list_shows_the_workspace_column(sandbox, model, capsys):
    model.answer("答")
    cli.main(["--agent", "--new-session", "问"])
    capsys.readouterr()

    assert cli.main(["--list-sessions"]) == 0

    row = capsys.readouterr().out.strip().splitlines()[0].split("\t")
    assert row[3].startswith("w-")


def test_permission_default_comes_from_the_workspace(sandbox, model, monkeypatch, capsys):
    """运行级旗标 > 工作区默认权限 > strict：这里验中间那一档。"""
    registry = cli.WorkspaceRegistry()
    ws = registry.add(sandbox, permission="workspace")
    seen = {}

    def fake_loop(messages, **kwargs):
        seen.update(kwargs)
        return "答"

    monkeypatch.setattr(cli, "agent_loop", fake_loop)

    assert cli.main(["--agent", "--new-session", "问"]) == 0

    assert seen["permission_mode"] == "workspace"
    assert seen["workspace_root"] == ws.root
    capsys.readouterr()


def test_run_flag_overrides_the_workspace_default(sandbox, model, monkeypatch, capsys):
    cli.WorkspaceRegistry().add(sandbox, permission="workspace")
    seen = {}

    def fake_loop(messages, **kwargs):
        seen.update(kwargs)
        return "答"

    monkeypatch.setattr(cli, "agent_loop", fake_loop)

    assert cli.main(["--agent", "--new-session", "--permission", "system", "问"]) == 0

    assert seen["permission_mode"] == "system"
    capsys.readouterr()


def test_workspace_subcommand_add_list_permission_remove(sandbox, capsys, tmp_path):
    other = tmp_path.parent / f"ws-{tmp_path.name}"
    other.mkdir()

    assert cli.main(["workspace", "add", str(other), "--name", "另一个"]) == 0
    added = capsys.readouterr().out.strip().split("\t")
    assert added[1] == str(other.resolve())
    assert added[2] == "另一个"

    assert cli.main(["workspace", "list"]) == 0
    assert "另一个" in capsys.readouterr().out

    assert cli.main(["workspace", "permission", added[0], "system"]) == 0
    assert capsys.readouterr().out.strip().split("\t")[1] == "system"

    assert cli.main(["workspace", "remove", added[0]]) == 0
    assert "磁盘上的会话数据未动" in capsys.readouterr().out
    assert cli.main(["workspace", "list"]) == 0
    assert "另一个" not in capsys.readouterr().out


def test_workspace_subcommand_reports_unknown(monkeypatch, capsys, tmp_path):
    assert cli.main(["workspace", "add", str(tmp_path / "missing")]) == 1

    assert "工作区错误" in capsys.readouterr().err


def test_cli_never_writes_the_registry(sandbox, model, capsys, tmp_path):
    """CLI 的读与跑都不写注册表：只有 `avid workspace add` 会写。

    启动/日常使用写盘会让"注册表里有什么"取决于你用没用过它，而不是你登记了什么。
    """
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
