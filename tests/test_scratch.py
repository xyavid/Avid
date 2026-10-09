"""Scratch sessions: the context is copied, and read-only holds through three independent guards —
the tool table, the read-only workspace mount, and the session marker every run of it reads.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, make_turn, wait_for

from avid.agent.state import RunState
from avid.agent.tools import TOOLS, build_toolset, readonly_names, specs, without_writers
from avid.security.sandbox import SandboxSpec, build_spec
from avid.services import Services
from avid.session import DEFAULT_BRANCH, session_scratch
from avid.web import create_app


def names(schemas: list[dict]) -> set[str]:
    return {item["function"]["name"] for item in schemas}


@pytest.fixture
def rig(sandbox):
    """A real service with a scripted model; runs use the production wiring and built-in table."""
    chat = ScriptedChat(make_turn("回答"), make_turn("回答"), make_turn("回答"))
    services = Services(root=sandbox / ".avid" / "sessions", chat=chat, workspace_root=None)
    client = TestClient(
        create_app(services=services, static_dir=sandbox / "static-not-built"),
        base_url="http://127.0.0.1:8765",
    )
    yield client, chat, services
    services.close()


@pytest.fixture
def client(rig):
    return rig[0]


def make_session(client: TestClient, *, workspace=None, seed: list[dict] | None = None) -> str:
    workspace = workspace or str(client.get("/api/workspaces").json()["workspaces"][0]["root"])
    created = client.post("/api/sessions", json={"workspace": workspace}).json()
    return created["id"]


# ---------------- context: a projected snapshot is copied ----------------


def test_scratch_copies_the_source_context_and_marks_the_session(rig):
    client, _, services = rig
    source = make_session(client)
    # Seed the source with real history (user + assistant); the scratch copy must carry it over
    with services.sessions._session(source) as session:
        branch = session.branch(DEFAULT_BRANCH) or session.create_branch(DEFAULT_BRANCH, None)
        branch.append_message({"role": "user", "content": "主线的问题"})
        branch.append_message({"role": "assistant", "content": "主线的回答"})

    response = client.post(f"/api/sessions/{source}/scratch", json={})
    assert response.status_code == 201, response.text
    scratch = response.json()

    assert scratch["copied_messages"] == 2
    assert scratch["parent_session_id"] == source
    assert scratch["id"] != source
    assert "临时对话" in (scratch["name"] or "")

    page = client.get(f"/api/sessions/{scratch['id']}/entries?order=asc").json()
    contents = [entry["message"]["content"] for entry in page["entries"]]
    assert contents == ["主线的问题", "主线的回答"]

    # The marker lives on the session: its value carries the source used to decide read-only
    with services.sessions._session(scratch["id"]) as opened:
        stored = opened.get_value(session_scratch())
    assert stored is not None and stored.value == {"source": source}

    # Snapshot semantics: the source keeps growing, the scratch copy does not
    with services.sessions._session(source) as session:
        session.branch(DEFAULT_BRANCH).append_message({"role": "user", "content": "之后又问了"})
    after = client.get(f"/api/sessions/{scratch['id']}/entries").json()
    assert len(after["entries"]) == 2


def test_scratch_of_an_unknown_session_is_a_404(client):
    assert client.post("/api/sessions/nope/scratch", json={}).status_code == 404


def test_scratch_session_can_be_deleted(client):
    source = make_session(client)
    scratch = client.post(f"/api/sessions/{source}/scratch", json={}).json()

    assert client.delete(f"/api/sessions/{scratch['id']}").status_code == 204
    ids = [item["id"] for item in client.get("/api/sessions").json()["sessions"]]
    assert scratch["id"] not in ids and source in ids


# ---------------- read-only guard 1: the tool table ----------------


def test_readonly_names_exclude_the_writing_tools():
    kept = readonly_names()

    assert {"read_file", "glob", "bash", "subagent"} <= kept
    assert "write_file" not in kept and "edit_file" not in kept


def test_readonly_toolset_drops_writers_from_any_table():
    schemas, impls = without_writers(TOOLS, {spec.name: spec.impl for spec in specs()})

    assert names(schemas) == readonly_names()
    assert set(impls) == readonly_names()


def test_build_toolset_drops_writers_and_mcp_for_a_scratch_run():
    class FakeMcp:
        def toolset(self):
            return (
                [{"type": "function", "function": {"name": "mcp__fs__write", "description": "", "parameters": {}}}],
                {"mcp__fs__write": lambda args: "ok"},
            )

    normal = RunState()
    normal.mcp = FakeMcp()
    schemas, impls = build_toolset(normal)
    assert "mcp__fs__write" in names(schemas) and "write_file" in names(schemas)

    scratch = RunState(scratch=True)
    scratch.mcp = FakeMcp()
    schemas, impls = build_toolset(scratch)
    # Writers dropped, MCP too: unknown external tools are not assumed read-only
    assert "write_file" not in names(schemas) and "edit_file" not in names(schemas)
    assert "mcp__fs__write" not in names(schemas) and "mcp__fs__write" not in impls


def test_a_scratch_run_hands_the_reduced_table_to_the_model(rig):
    """Wiring-level: a scratch run sends no writing tools; a mainline run of it still does."""
    client, chat, _ = rig
    source = make_session(client)
    scratch = client.post(f"/api/sessions/{source}/scratch", json={}).json()

    run = client.post(f"/api/sessions/{scratch['id']}/runs", json={"prompt": "问一句", "auto_approve": True})
    assert run.status_code == 201, run.text
    assert wait_for(lambda: client.get(f"/api/runs/{run.json()['run_id']}").json()["status"] == "finished")

    scratch_sent = names(chat.requests[-1]["tools"])
    assert "write_file" not in scratch_sent and "edit_file" not in scratch_sent
    assert "read_file" in scratch_sent

    # Same path on a mainline session: writers are back, read-only is per scratch session
    run = client.post(f"/api/sessions/{source}/runs", json={"prompt": "问一句", "auto_approve": True})
    assert run.status_code == 201, run.text
    assert wait_for(lambda: client.get(f"/api/runs/{run.json()['run_id']}").json()["status"] == "finished")
    normal_sent = names(chat.requests[-1]["tools"])
    assert {"write_file", "edit_file"} <= normal_sent


# ---------------- read-only guard 2: the sandbox ----------------


def test_readonly_sandbox_mounts_the_workspace_read_only():
    spec = SandboxSpec(
        policy="workspace",
        available=True,
        binary="/usr/bin/bwrap",
        root="/tmp/ws",
        read_only=True,
    )
    argv = spec.argv_prefix(["ls"], root="/tmp/ws")

    assert argv[argv.index("--ro-bind", argv.index("--tmpfs")) :][:3] == ["--ro-bind", "/tmp/ws", "/tmp/ws"]
    assert "--bind" not in argv
    assert spec.summary()["read_only"] is True
    assert "只读" in spec.one_line()


def test_normal_sandbox_still_mounts_the_workspace_read_write():
    spec = SandboxSpec(policy="workspace", available=True, binary="/usr/bin/bwrap", root="/tmp/ws")
    argv = spec.argv_prefix(["ls"], root="/tmp/ws")

    assert "--bind" in argv and spec.summary()["read_only"] is False


def test_build_spec_carries_the_readonly_flag_and_says_so():
    spec = build_spec(root=None, read_only=True)

    assert spec.read_only is True
    assert "工作区挂只读（临时对话）" in spec.notes


# ---------------- read-only guard 3: child runs inherit it ----------------


def test_child_runs_of_a_scratch_parent_get_the_reduced_table(monkeypatch):
    from avid.agent import run as run_module
    from avid.agent.tools import subagent as subagent_module

    specs: list[object] = []

    class FakeRun:
        def __init__(self, messages, spec, **kwargs):
            specs.append(spec)

        def run(self):
            class _Outcome:
                text = "摘要"

            return _Outcome()

    monkeypatch.setattr(run_module, "Run", FakeRun)
    config = __import__("avid.providers.config", fromlist=["Config"]).Config(
        api_key="k", base_url="https://api.test/v1", model="m"
    )

    subagent_module.run_subagent("任务", config=config, scratch=True)
    scratch_names = names(specs[-1].tools)
    subagent_module.run_subagent("任务", config=config, scratch=False)
    normal_names = names(specs[-1].tools)

    assert "write_file" not in scratch_names and "edit_file" not in scratch_names
    assert "read_file" in scratch_names
    assert {"write_file", "edit_file"} <= normal_names
