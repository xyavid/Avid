"""Session worktrees: which root one run works on, and where that root comes from.

Failure list pinned here: a workspace that is not a repo, is not the repo's top level, or has no
commit must fall back to the workspace root with a reason instead of failing the run; the workspace
root belongs to one session at a time, so the second session of that workspace gets its own tree;
a session's tree is decided once, so a later run lands on the same path and branch; a tree deleted
outside Avid is rebuilt from the record, while one whose branch is gone too stops the run instead of
silently moving the session elsewhere; a session id that is not a legal path or branch name is
sanitized; a tree with work in it is never thrown away; a run's tools, sandbox and checkpoints all
follow the root the session was given.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import (
    ScriptedChat,
    collect,
    git,
    init_repo,
    make_turn,
    new_session,
    tool_call,
    wait_handle_released,
    wait_terminal,
)

from avid.agent.events import RUN_STARTED
from avid.security import userdirs
from avid.services import Services
from avid.services.workspaces import bound_workspace
from avid.services.worktrees import (
    WorktreeError,
    facts,
    recorded,
    release,
    resolve,
)
from avid.session import JsonlSessionRepo


def make_repo(tmp_path: Path, name: str = "proj") -> Path:
    """A repo with one commit, in its own directory under tmp_path."""
    return init_repo(tmp_path / name)


def take_root(open_session, workspace, session_id: str = "s-owner"):
    """A session that already works on the workspace root, so the next one needs a tree of its own."""
    return resolve(open_session(session_id=session_id), workspace)


@pytest.fixture
def open_session(tmp_path):
    """Sessions in their own store, one repository per workspace id; closed at teardown."""
    repos: list[JsonlSessionRepo] = []

    def make(workspace_id: str = "w-1", session_id: str = "s-1"):
        repo = JsonlSessionRepo(tmp_path / "sessions" / workspace_id, workspace=workspace_id)
        repos.append(repo)
        return repo.create(id=session_id)

    yield make
    for repo in repos:
        repo.close()


# ---------------- the four fallbacks ----------------


def test_a_workspace_that_is_not_a_repo_keeps_its_own_root(tmp_path, open_session):
    plain = tmp_path / "plain"
    plain.mkdir()
    workspace = bound_workspace(plain)
    session = open_session()

    decided = resolve(session, workspace)

    assert Path(decided.path) == plain.resolve()
    assert decided.branch is None
    assert decided.base is None
    assert decided.reason and "不是 git 仓库" in decided.reason
    assert not (userdirs.worktrees_dir() / workspace.id).exists()


def test_a_workspace_below_the_repo_top_level_keeps_its_own_root(tmp_path, open_session):
    """A tree is repo-wide, so a workspace that is one directory of a repo cannot have a narrower
    one: the worktree would reach outside the workspace the sandbox is rooted at.
    """
    root = make_repo(tmp_path)
    inside = root / "web"
    inside.mkdir()
    session = open_session()

    decided = resolve(session, bound_workspace(inside))

    assert Path(decided.path) == inside.resolve()
    assert decided.branch is None
    assert decided.reason and "仓库根" in decided.reason


def test_a_repo_without_a_commit_keeps_its_own_root(tmp_path, open_session):
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    session = open_session()

    decided = resolve(session, bound_workspace(root))

    assert decided.branch is None
    assert decided.reason and "还没有提交" in decided.reason


def test_a_scratch_session_never_gets_a_tree(tmp_path, open_session):
    """Scratch runs are read-only (tools and sandbox alike), and a tree is only worth its cost
    when two runs can write at once.
    """
    root = make_repo(tmp_path)
    session = open_session()

    decided = resolve(session, bound_workspace(root), scratch=True)

    assert Path(decided.path) == root.resolve()
    assert decided.branch is None
    assert decided.reason and "白板" in decided.reason


# ---------------- allocation ----------------


def test_the_first_session_of_a_workspace_owns_the_root(tmp_path, open_session):
    root = make_repo(tmp_path)
    session = open_session()

    decided = resolve(session, bound_workspace(root))

    assert Path(decided.path) == root.resolve()
    assert decided.branch is None
    assert decided.reason is None


def test_the_second_session_of_a_workspace_gets_its_own_tree(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    first = open_session(session_id="s-first")
    second = open_session(session_id="s-second")
    resolve(first, workspace)

    decided = resolve(second, workspace)

    assert decided.branch == "avid/session-s-second"
    assert Path(decided.path) == (userdirs.worktrees_dir() / workspace.id / "s-second").resolve()
    assert decided.base == git(root, "rev-parse", "HEAD")
    assert Path(decided.path).is_dir()
    # A real worktree of the same repository: a write there never reaches the workspace root.
    (Path(decided.path) / "b.txt").write_text("b\n", encoding="utf-8")
    assert not (root / "b.txt").exists()
    assert Path(git(Path(decided.path), "rev-parse", "--show-toplevel")).resolve() == Path(
        decided.path
    ).resolve()
    # The workspace's own checkout stays where it was: the tree is a second checkout, not a switch.
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_the_tree_is_decided_once_and_survives_a_reopen(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    first = resolve(session, workspace)

    again = resolve(session, workspace)
    assert recorded(session) == first

    # The decision lives in the session file, not in this process: a later run reads it back.
    session.close()
    repo = JsonlSessionRepo(tmp_path / "sessions" / "w-1", workspace="w-1")
    try:
        reopened = repo.open(next(item for item in repo.list() if item.id == "s-second"))
        after_reopen = resolve(reopened, workspace)
        assert recorded(reopened) == first
    finally:
        repo.close()

    assert (again.path, again.branch, again.base) == (first.path, first.branch, first.base)
    assert (after_reopen.path, after_reopen.branch) == (first.path, first.branch)
    # A second decision would have created a second branch under the same prefix.
    branches = git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/avid/")
    assert branches == first.branch, branches


def test_another_workspace_keeps_its_own_root(tmp_path, open_session):
    alpha = make_repo(tmp_path, "alpha")
    beta = make_repo(tmp_path, "beta")
    first = open_session(workspace_id="w-a", session_id="s-a")
    second = open_session(workspace_id="w-b", session_id="s-b")
    resolve(first, bound_workspace(alpha))

    decided = resolve(second, bound_workspace(beta))

    assert Path(decided.path) == beta.resolve()
    assert decided.branch is None


# ---------------- recovery ----------------


def test_a_deleted_tree_is_rebuilt_from_the_record(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    first = resolve(session, workspace)
    subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", first.path], check=True)
    assert not Path(first.path).exists()

    again = resolve(session, workspace)

    assert (again.path, again.branch, again.base) == (first.path, first.branch, first.base)
    assert Path(again.path).is_dir()


def test_a_tree_whose_branch_is_gone_stops_this_run(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    first = resolve(session, workspace)
    subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", first.path], check=True)
    git(root, "branch", "-D", first.branch)

    with pytest.raises(WorktreeError) as raised:
        resolve(session, workspace)

    message = str(raised.value)
    assert first.path in message and first.branch in message


# ---------------- naming ----------------


def test_a_session_id_is_sanitized_into_a_path_and_a_branch(tmp_path, open_session):
    """Ids are free-form apart from the three characters the file layout forbids, so both the
    directory name and the branch have to be reduced to what the filesystem and git accept.
    """
    root = make_repo(tmp_path)
    session = open_session(session_id="s 1:2*3")
    workspace = bound_workspace(root)
    take_root(open_session, workspace)

    decided = resolve(session, workspace)

    name = Path(decided.path).name
    assert name and all(char.isalnum() or char in "._-" for char in name), name
    # git accepts the branch: check-ref-format exits non-zero for a name it would refuse.
    git(root, "check-ref-format", "--branch", decided.branch)
    assert git(root, "rev-parse", "--verify", decided.branch)


# ---------------- release ----------------


def test_a_dirty_tree_is_kept_when_the_session_goes(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    decided = resolve(session, workspace)
    (Path(decided.path) / "b.txt").write_text("b\n", encoding="utf-8")

    note = release(session, workspace)

    assert note and "未提交" in note
    assert Path(decided.path).is_dir()


def test_a_clean_tree_with_no_commit_goes_with_its_branch(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    decided = resolve(session, workspace)

    note = release(session, workspace)

    assert note is None
    assert not Path(decided.path).exists()
    assert decided.branch not in git(root, "branch", "--list", "--format=%(refname:short)")
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_a_clean_tree_with_commits_keeps_its_branch(tmp_path, open_session):
    """The branch is the session's work; only the checkout is disposable."""
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    decided = resolve(session, workspace)
    (Path(decided.path) / "b.txt").write_text("b\n", encoding="utf-8")
    git(Path(decided.path), "add", "-A")
    git(Path(decided.path), "commit", "-qm", "会话做的")

    note = release(session, workspace)

    assert note is None
    assert not Path(decided.path).exists()
    assert decided.branch in git(root, "branch", "--list", "--format=%(refname:short)")


def test_releasing_hands_the_root_to_the_next_session(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    first = open_session(session_id="s-first")
    second = open_session(session_id="s-second")
    resolve(first, workspace)

    release(second, workspace)  # a session that never had anything: still safe
    release(first, workspace)

    again = open_session(session_id="s-third")
    decided = resolve(again, workspace)
    assert Path(decided.path) == root.resolve()
    assert decided.branch is None


# ---------------- the panel's reading ----------------


def test_facts_report_dirty_files_commits_and_how_far_ahead(tmp_path, open_session):
    root = make_repo(tmp_path)
    workspace = bound_workspace(root)
    take_root(open_session, workspace)
    session = open_session(session_id="s-second")
    decided = resolve(session, workspace)
    (Path(decided.path) / "b.txt").write_text("b\n", encoding="utf-8")
    (Path(decided.path) / "a.txt").write_text("changed\n", encoding="utf-8")
    git(Path(decided.path), "add", "b.txt")
    git(Path(decided.path), "commit", "-qm", "加一个文件")

    reading = facts(decided)

    assert reading["branch"] == decided.branch
    assert sorted(item["path"] for item in reading["dirty"]) == ["a.txt"]
    assert [item["subject"] for item in reading["commits"]] == ["加一个文件"]
    assert reading["ahead"] == 1


def test_facts_on_a_workspace_root_say_so(tmp_path, open_session):
    plain = tmp_path / "plain"
    plain.mkdir()
    decided = resolve(open_session(), bound_workspace(plain))

    reading = facts(decided)

    assert reading["worktree"] is False
    assert Path(reading["path"]) == plain.resolve()
    assert "不是 git 仓库" in reading["reason"]


# ---------------- one run, two sessions ----------------


def test_a_run_of_the_second_session_writes_inside_its_own_tree(tmp_path):
    """Two sessions on one repo: the run reports where it works and the tools really work there.
    The first session stays on the checkout, so its own writes land where they always did.
    """
    root = make_repo(tmp_path)
    seen: list[str] = []

    def write_probe(arguments, **kwargs):
        where = kwargs["state"].workspace_root
        seen.append(where)
        (Path(where) / "probe.txt").write_text("x", encoding="utf-8")
        return "写好了"

    turn = make_turn("", [tool_call("write_file")])
    services = Services(
        workspace_root=str(root),
        chat=ScriptedChat(turn, make_turn("完成"), turn, make_turn("完成")),
        tool_registry={"write_file": write_probe},
    )
    try:
        first = services.runs.start(new_session(services), "先来")
        assert wait_terminal(first), first.status
        second = services.runs.start(new_session(services), "再来")
        assert wait_terminal(second), second.status

        opened = {
            record.session_id: next(
                event.data
                for event in collect(services, record.run_id)
                if event.type == RUN_STARTED
            )
            for record in (first, second)
        }
    finally:
        services.close()

    assert Path(opened[first.session_id]["worktree"]["path"]) == root.resolve()
    assert opened[first.session_id]["worktree"]["branch"] is None
    tree = opened[second.session_id]["worktree"]
    assert tree["branch"].startswith("avid/session-")
    assert userdirs.worktrees_dir() in Path(tree["path"]).parents
    # Both runs' tools used the root their own session was given.
    assert seen == [str(root.resolve()), tree["path"]]
    assert (Path(tree["path"]) / "probe.txt").exists()
    assert git(root, "status", "--porcelain").strip() == "?? probe.txt"


def test_deleting_a_session_gives_its_tree_and_the_root_back(tmp_path):
    """Deleting the sessions releases both: the tree goes with its empty branch, and the workspace
    root becomes free, so the next session works on it instead of opening a third tree.
    """
    root = make_repo(tmp_path)
    services = Services(
        workspace_root=str(root),
        chat=ScriptedChat(*[make_turn("完成")] * 3),
        tool_registry={},
    )
    try:
        first = new_session(services)
        second = new_session(services)
        for session_id, prompt in ((first, "先来"), (second, "再来")):
            assert wait_terminal(services.runs.start(session_id, prompt))
            wait_handle_released(services, session_id)
        tree = Path(services.sessions.worktree(second)["path"])
        assert tree.is_dir()

        note = services.sessions.delete(second)
        assert note is None
        services.sessions.delete(first)

        assert not tree.exists()
        third = new_session(services)
        assert wait_terminal(services.runs.start(third, "三来"))
        reading = services.sessions.worktree(third)
        assert Path(reading["path"]) == root.resolve()
        assert reading["branch"] is None
    finally:
        services.close()
