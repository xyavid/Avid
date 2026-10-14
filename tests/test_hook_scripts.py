"""Hook scripts declared in the user-level ``hooks.json``: what they receive, what they may
answer, and the failure list.

Failure list this file pins: a missing file loads nothing; a broken declaration skips entries with
a warning instead of failing the run; a script that crashes, exits non-zero, times out, prints
garbage or answers an unknown verdict blocks the step (fail closed); only declared fields reach the
subprocess, so kernel objects (the security spec, the approval callback) never do.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from avid.agent import hooks as hooks_module
from avid.agent.hooks import EVENTS, HookRegistry, load_hooks
from avid.security import userdirs

# Scripts are real subprocesses; this one is the interpreter running the tests.
PY = sys.executable


def write_config(entries: list[dict]) -> Path:
    """The declaration a run reads: one user-level file, as the fixture's AVID_HOME points it out."""
    path = userdirs.hooks_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": entries}), encoding="utf-8")
    return path


def load() -> HookRegistry:
    """Load the declaration the way a run does, through the same single resolution point."""
    return load_hooks(userdirs.hooks_config_path())


def script(body: str, *args: str) -> dict:
    """One declaration whose command runs `body` as Python code."""
    return {
        "event": "PreToolUse",
        "command": PY,
        "args": ["-c", body, *args],
    }


def answer(decision: str = "allow", **extra) -> str:
    payload = {"decision": decision, **extra}
    return f"import json,sys; print(json.dumps({payload!r}))"


# ---- loading the declaration ----


def test_no_config_file_loads_nothing(tmp_path):
    registry = load()

    assert isinstance(registry, HookRegistry)
    for event in EVENTS:
        assert registry.registered(event) == []


def test_a_broken_file_skips_but_does_not_fail(tmp_path, caplog):
    path = userdirs.hooks_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是 JSON", encoding="utf-8")

    with caplog.at_level("WARNING", logger="avid.agent.hooks"):
        registry = load()

    assert registry.registered("PreToolUse") == []
    assert any("无法解析" in record.getMessage() for record in caplog.records)


def test_entries_missing_a_field_are_skipped_one_by_one(tmp_path, caplog):
    write_config(
        [
            {"command": PY},  # no event
            {"event": "PreToolUse"},  # no command
            {"event": "pretooluse", "command": PY},  # unknown event
            {"event": "PreToolUse", "command": PY, "args": ["-c", answer("allow")]},
        ],
    )

    with caplog.at_level("WARNING", logger="avid.agent.hooks"):
        registry = load()

    assert len(registry.registered("PreToolUse")) == 1
    assert len([r for r in caplog.records if "跳过" in r.getMessage()]) == 3


def test_every_event_can_be_declared(tmp_path):
    write_config(
        [
            {"event": event, "command": PY, "args": ["-c", answer("allow")]}
            for event in EVENTS
        ],
    )

    registry = load()

    for event in EVENTS:
        assert len(registry.registered(event)) == 1


# ---- what a script receives ----


def test_only_declared_fields_reach_the_subprocess(tmp_path):
    """The context also carries kernel objects; a subprocess must never receive them."""
    dump = tmp_path / "seen.json"
    write_config(
        [script("import json,sys; d=json.load(sys.stdin); open(sys.argv[1],'w').write(json.dumps(sorted(d)))", str(dump))],
    )
    registry = load()
    context = {
        "event": "PreToolUse",
        "tool": "bash",
        "arguments": {"command": "ls"},
        "round": 2,
        "tool_call_id": "c1",
        "workspace_root": str(tmp_path),
        # Kernel objects that must not travel:
        "security": object(),
        "ask": lambda *args: True,
        "approval_ledger": object(),
    }

    registry.trigger("PreToolUse", context)

    assert json.loads(dump.read_text(encoding="utf-8")) == [
        "arguments",
        "event",
        "round",
        "tool",
        "tool_call_id",
        "workspace_root",
    ]


def test_the_event_name_and_payload_travel_on_stdin(tmp_path):
    dump = tmp_path / "called.json"
    write_config(
        [
            {
                "event": "PostToolUse",
                "command": PY,
                "args": [
                    "-c",
                    "import json,sys; d=json.load(sys.stdin); open(sys.argv[1],'w').write(json.dumps(d))",
                    str(dump),
                ],
            }
        ],
    )
    registry = load()

    registry.trigger(
        "PostToolUse",
        {"content": "结果", "tool": "read_file", "truncated": False, "round": 1},
    )

    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert payload["event"] == "PostToolUse"
    assert payload["content"] == "结果" and payload["truncated"] is False


# ---- what a script may answer ----


def test_allow_lets_the_step_through(tmp_path):
    write_config([script(answer("allow"))])
    registry = load()

    assert registry.trigger("PreToolUse", {"tool": "bash"}) == "allow"


def test_block_carries_the_reason_back(tmp_path):
    write_config([script(answer("block", reason="本项目禁止改这个文件"))])
    registry = load()
    context = {"tool": "write_file"}

    assert registry.trigger("PreToolUse", context) == "block"
    assert context["denied_reason"] == "本项目禁止改这个文件"
    # The reason is what the model reads, so a hook can explain itself instead of printing a slogan
    assert context["denied_content"] == "本项目禁止改这个文件"


def test_an_injection_lands_in_the_context(tmp_path):
    write_config(
        [
            {
                "event": "UserPromptSubmit",
                "command": PY,
                "args": ["-c", answer("allow", injected="[环境] 今天是发布日")],
            }
        ],
    )
    registry = load()
    context: dict = {"prompt": "发版", "injected": []}

    assert registry.trigger("UserPromptSubmit", context) == "allow"
    assert context["injected"] == ["[环境] 今天是发布日"]


def test_an_injection_on_another_event_is_ignored_with_a_warning(tmp_path, caplog):
    """Only UserPromptSubmit has somewhere to put injected text; the rest must say so."""
    write_config([script(answer("allow", injected="乱塞的文本"))])
    registry = load()
    context: dict = {"tool": "bash"}

    with caplog.at_level("WARNING", logger="avid.agent.hooks"):
        registry.trigger("PreToolUse", context)

    assert "injected" not in context
    assert any("injected" in record.getMessage() for record in caplog.records)


def test_a_stop_block_supplies_the_nudge(tmp_path):
    write_config(
        [
            {
                "event": "Stop",
                "command": PY,
                "args": ["-c", answer("block", reason="还有一步没做完")],
            }
        ],
    )
    registry = load()
    context: dict = {"final_text": "好了"}

    assert registry.trigger("Stop", context) == "block"
    assert context["nudge"] == "还有一步没做完"


# ---- failure is a block, never a silent pass ----


def test_a_nonzero_exit_blocks(tmp_path, caplog):
    write_config(
        [script("import sys; sys.stderr.write('坏了'); sys.exit(3)")],
    )
    registry = load()
    context = {"tool": "bash"}

    with caplog.at_level("WARNING", logger="avid.agent.hooks"):
        assert registry.trigger("PreToolUse", context) == "block"

    assert "3" in context["denied_reason"]


def test_unparseable_output_blocks(tmp_path):
    write_config([script("print('不是 JSON')")])
    registry = load()

    assert registry.trigger("PreToolUse", {"tool": "bash"}) == "block"


def test_an_unknown_verdict_blocks(tmp_path):
    write_config([script(answer("maybe"))])
    registry = load()

    assert registry.trigger("PreToolUse", {"tool": "bash"}) == "block"


def test_a_timeout_blocks(tmp_path):
    write_config(
        [script("import time; time.sleep(5)") | {"timeout_seconds": 0.2}],
    )
    registry = load()

    assert registry.trigger("PreToolUse", {"tool": "bash"}) == "block"


def test_a_missing_command_blocks(tmp_path):
    write_config([{"event": "PreToolUse", "command": "/definitely/not/here"}])
    registry = load()

    assert registry.trigger("PreToolUse", {"tool": "bash"}) == "block"


# ---- wiring: a blocking script really stops the call ----


def test_a_blocking_script_stops_the_tool_call(tmp_path):
    """End to end through execute_one: the handler never runs and the reason reaches the model."""
    from avid.agent.execution import execute_one
    from avid.agent.state import RunState

    write_config([script(answer("block", reason="这个仓库禁止执行 bash"))])
    state = RunState.for_run(
        hooks=load(), auto_approve=True, audit_enabled=False
    )
    executed = []

    content = execute_one(
        "bash",
        '{"command": "ls"}',
        {"bash": lambda arguments, **kwargs: executed.append(arguments) or "不该执行"},
        state=state,
        round_index=0,
    )

    assert executed == []
    assert "这个仓库禁止执行 bash" in content
    assert state.denials == 1


def test_an_allow_script_does_not_disturb_the_call(tmp_path):
    from avid.agent.execution import execute_one
    from avid.agent.state import RunState

    write_config([script(answer("allow"))])
    state = RunState.for_run(
        hooks=load(), auto_approve=True, audit_enabled=False
    )

    content = execute_one(
        "bash",
        '{"command": "echo hi"}',
        {"bash": lambda arguments, **kwargs: "hi"},
        state=state,
        round_index=0,
    )

    assert content == "hi" and state.denials == 0


def test_the_loader_is_the_single_entry_the_run_uses(tmp_path):
    """RunState.for_run with no injected registry loads the user-level declaration."""
    from avid.agent.state import RunState

    write_config([script(answer("block"))])
    state = RunState.for_run(
        workspace_root=str(tmp_path), auto_approve=True, audit_enabled=False
    )

    assert len(state.hooks.registered("PreToolUse")) == 1

    # The declaration is one user-level file, so nothing a run writes inside its workspace is a hook.
    assert userdirs.hooks_config_path() == userdirs.avid_home() / "hooks.json"
    assert not (tmp_path / ".avid").exists()
    assert hooks_module.load_hooks.__module__ == "avid.agent.hooks"
