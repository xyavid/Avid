"""CLI 的会话旗标：新建、续接、列举、销毁。

端到端跑真 CLI：只把模型换成 ``FakeChat``（替换 ``cli.agent_loop`` 注入），
循环、会话、文件落盘都是真的。工作区根目录被换成 tmp_path，会话因此写在
临时目录下的 ``.avid/sessions/``。
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
    """按需安装一个返回固定回答的假模型（换掉 spec.chat，其余走真 Run）。"""

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
    # 模型配置由 conftest 的 model_env 种好（test/test-model）
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
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
        # --permission 已删（阶段 51）：权限模式不存在，完全访问只有 --allow-full-access。
        ["--agent", "--permission", "auto", "问"],
    ],
)
def test_argument_errors_exit_2(sandbox, argv):
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2


def test_no_prompt_enters_the_interactive_session(sandbox, monkeypatch):
    """无参数 = 交互会话（默认续接最近会话）；EOF 立即退出且返回 0。"""

    def eof(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", eof)

    assert cli.main(["--new-session"]) == 0
    assert cli.main([]) == 0


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


def test_run_state_carries_the_workspace_and_the_default_permission(sandbox, model, monkeypatch, capsys):
    """没有工作区默认权限、也没有 --permission：不给旗标就是 normal，沙箱照常请求强制隔离。"""
    # 沙箱后端是宿主事实（CI runner 上通常没有 bwrap）：注入一个可用探针，把断言钉在
    # 「CLI 请求了强制隔离」而不是「这台机器装了 bwrap」。
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

    # 权限与工作区根都装在那份运行级规格里（state.security）：少一处转发就是
    # "界面说 normal、实际按别的形态跑"，所以断言读的是**真正传给循环的那份 state**。
    state = seen.get("state")
    assert state.permission_mode == "normal"
    assert state.workspace_root == ws.root
    assert state.security.full is False
    assert state.security.sandbox.policy == "workspace"
    assert state.security.sandbox.enforced is True
    capsys.readouterr()


def test_allow_full_access_turns_on_full_permission(sandbox, model, monkeypatch, capsys):
    """完全访问的唯一开关是 --allow-full-access：跳过毁灭级确认、关沙箱。"""
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

    # `workspace permission` 子命令已删（阶段 51）：argparse 直接拒。
    with pytest.raises(SystemExit) as info:
        cli.main(["workspace", "permission", added[0], "auto"])
    assert info.value.code == 2

    assert cli.main(["workspace", "remove", added[0]]) == 0
    # 摘掉 = 立墓碑：候选里没有了，但会话与条目都留着（`add` 同一个路径即撤销）。
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


def test_interactive_turn_skill_and_unknown_command(sandbox, model, monkeypatch, capsys, tmp_path):
    """交互会话：普通输入走一次运行；/技能 全文写入会话；未知命令给提示不发模型。"""
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
    assert "/compact" in err and "/demo" in err   # 未知命令提示列出可用命令与技能
    assert "没有可压缩的更早历史" in err

    # 技能全文确实写入了会话（落库、可续接）
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
    """/rewind：文件恢复到该输入之前，第二问及其全部后续移出对话（孤儿留在盘上）。"""
    # Model.answer 只会包装纯文本回合；这里要真 write_file（写前快照 + 落盘），自己接 FakeRun。
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

