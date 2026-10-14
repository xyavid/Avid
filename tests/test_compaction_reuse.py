"""Compaction over the session prefix: the request carries the caller's system prompt, tool table
and the pre-cut history verbatim, with the task block appended as the one new message.

The failure list this file pins: a tool call, an empty answer or a raised error in the reuse
attempt must fall back once to the engine-prompt path; a missing prefix must not produce a
half-reused request; the checkpoint contract, the kept window and the cursor do not change.
"""

import pytest

from avid.agent.compaction import (
    CHECKPOINT_STRUCTURE,
    COMPACTION_SYSTEM,
    CREATE_TASK,
    UPDATE_TASK,
    ContextBudget,
    cut_point,
    run_compaction,
)
from avid.agent.state import RunState
from avid.agent.transcript import Transcript
from avid.providers.client import LLMError, Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")
SESSION_SYSTEM = "你是 Avid，一个编码代理。"
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]


@pytest.fixture(autouse=True)
def spill_root(tmp_path, monkeypatch):
    """Spills go to a temp workspace so tests do not dirty the repo."""
    from avid.agent.tools import workspace

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


def turn(text="摘要正文", calls=(), usage=None):
    return Turn(
        message={"role": "assistant", "content": text, "tool_calls": list(calls)},
        text=text,
        tool_calls=list(calls),
        usage=Usage(1, 1, 2) if usage is None else usage,
        model="m",
        finish_reason="stop",
    )


class ScriptedChat:
    """Answers with the scripted turns in order (an Exception is raised) and records requests."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        if not self.script:
            return turn()
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def user(text="hi"):
    return {"role": "user", "content": text}


def rounds(count, content="x" * 200):
    messages = [user("任务")]
    for index in range(count):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{index}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": content})
    return messages


def budget(**overrides):
    defaults = {"keep_recent_turns": 10, "context_chars": 10, "from_window": False}
    defaults.update(overrides)
    return ContextBudget(**defaults)


def compact(chat, *, system=SESSION_SYSTEM, tools=TOOLS, transcript=None, state=None, **kwargs):
    return run_compaction(
        transcript=transcript or Transcript(rounds(12)),
        state=state or RunState(),
        config=CONFIG,
        chat=chat,
        limits=budget(),
        system=system,
        tools=tools,
        **kwargs,
    )


# ---- request shape: the prefix is the caller's, the instruction is the only new part ----


def test_reuse_request_repeats_system_tools_and_pre_cut_history(spill_root):
    chat = ScriptedChat()
    transcript = Transcript(rounds(12))
    cut = cut_point(Transcript(rounds(12)), 10)  # read before the run replaces the history

    compact(chat, transcript=transcript)

    request = chat.requests[0]
    assert request["system"] == SESSION_SYSTEM
    assert request["tools"] == TOOLS
    # Everything before the cut goes out verbatim: it is the prefix the last round already paid for
    assert request["messages"][:-1] == rounds(12)[:cut]
    assert len(request["messages"]) == cut + 1


def test_reuse_instruction_is_marked_like_an_injected_tail(spill_root):
    """Without the tail mark the reasoning replay plan empties and the prefix breaks there."""
    chat = ScriptedChat()

    compact(chat)

    instruction = chat.requests[0]["messages"][-1]
    assert instruction["tail"] is True
    assert instruction["role"] == "user"
    assert "material to compact" in instruction["content"]


def test_reuse_instruction_keeps_the_task_block_verbatim(spill_root):
    chat = ScriptedChat()

    compact(chat)

    body = chat.requests[0]["messages"][-1]["content"]
    assert CREATE_TASK in body
    for section in CHECKPOINT_STRUCTURE.splitlines():
        assert section in body, section
    assert "above is the material to compact" in body


def test_reuse_instruction_drops_the_material_that_is_already_in_context(spill_root):
    """The flattened copy and the re-sent checkpoint would both be the material twice over."""
    chat = ScriptedChat()

    compact(chat)

    body = chat.requests[0]["messages"][-1]["content"]
    assert "<conversation>" not in body
    assert "<original-request>" not in body
    assert "<previous-checkpoint>" not in body
    # The material itself must not be in the instruction: it rides in the messages
    assert "x" * 200 not in body


def test_reuse_update_instruction_points_at_the_checkpoint_in_context(spill_root, monkeypatch):
    from avid.agent import compaction as compaction_module

    monkeypatch.setattr(
        compaction_module, "repo_state", lambda root: "$ git status --porcelain -uno\n M a.py"
    )
    chat = ScriptedChat(turn("## Goal\nG"), turn("## Goal\nG2"))
    transcript = Transcript(rounds(12))
    state = RunState()

    compact(chat, transcript=transcript, state=state)
    transcript.append_many([user("接着干"), *rounds(2)[1:]])
    compact(chat, transcript=transcript, state=state, force=True)

    body = chat.requests[1]["messages"][-1]["content"]
    assert UPDATE_TASK in body
    assert CREATE_TASK not in body
    assert "first message" in body  # where the previous checkpoint sits
    assert "<repo-state>" in body and "M a.py" in body


def test_reuse_create_instruction_never_probes_the_repo(spill_root, monkeypatch):
    from avid.agent import compaction as compaction_module

    monkeypatch.setattr(
        compaction_module, "repo_state", lambda root: pytest.fail("首次压缩不该探仓库")
    )
    chat = ScriptedChat()

    compact(chat)

    assert "<repo-state>" not in chat.requests[0]["messages"][-1]["content"]


def test_reuse_request_is_not_capped(spill_root):
    chat = ScriptedChat()

    compact(chat)

    assert "max_tokens" not in chat.requests[0] or chat.requests[0]["max_tokens"] is None


# ---- fallbacks: a tool call, an empty answer or an error go back to the engine prompt ----


def test_a_tool_call_abandons_the_reuse_attempt_without_executing_it(spill_root):
    call = {
        "id": "c9",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
    }
    chat = ScriptedChat(turn("", [call]), turn("## Goal\n检查点"))

    report = compact(chat)

    assert report is not None
    assert len(chat.requests) == 2
    # The fallback is the engine-prompt path: its own system, no tools, flattened material
    assert chat.requests[1]["system"] == COMPACTION_SYSTEM
    assert chat.requests[1].get("tools") is None
    assert "<conversation>" in chat.requests[1]["messages"][0]["content"]


def test_an_empty_answer_falls_back_once(spill_root):
    chat = ScriptedChat(turn("", []) , turn("## Goal\n检查点"))

    report = compact(chat)

    assert report is not None
    assert len(chat.requests) == 2
    assert chat.requests[1]["system"] == COMPACTION_SYSTEM


def test_an_error_falls_back_once(spill_root):
    chat = ScriptedChat(LLMError("端点挂了"), turn("## Goal\n检查点"))

    report = compact(chat)

    assert report is not None
    assert len(chat.requests) == 2
    assert chat.requests[1]["system"] == COMPACTION_SYSTEM


def test_both_paths_failing_keeps_the_history(spill_root):
    transcript = Transcript(rounds(12))
    chat = ScriptedChat(LLMError("先挂"), LLMError("再挂"))

    report = compact(chat, transcript=transcript)

    assert report is None
    assert len(chat.requests) == 2
    assert transcript.as_messages() == rounds(12)


def test_a_failed_reuse_attempt_does_not_touch_the_transcript(spill_root):
    call = {"id": "c9", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    transcript = Transcript(rounds(12))
    chat = ScriptedChat(turn("", [call]), turn("## Goal\n检查点"))

    compact(chat, transcript=transcript)

    messages = transcript.as_messages()
    assert messages[0]["content"].startswith("[历史摘要]")
    assert len(messages) == 1 + 20  # one summary + the kept 10 rounds


# ---- usage reading of the compaction call itself ----


def test_compaction_call_usage_and_path_land_in_the_report(spill_root):
    """The summary call's own tokens are observable, with the route that produced it."""
    chat = ScriptedChat(turn("## Goal\n检查点", usage=Usage(1_000, 40, 1_040, cache_read_tokens=900)))
    state = RunState()

    compact(chat, state=state)

    call = state.usage_report()["compaction"]["last_call"]
    assert call["path"] == "reuse"
    assert call["attempts"] == 1
    assert call["prompt_tokens"] == 1_000
    assert call["completion_tokens"] == 40
    assert call["cache_read_tokens"] == 900
    assert call["hit_ratio"] == pytest.approx(0.9)


def test_a_failed_reuse_attempt_is_summed_into_the_engine_path_reading(spill_root):
    """Both attempts were paid for: the reading is their sum, and the route names the one that won."""
    call = {
        "id": "c9",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }
    chat = ScriptedChat(
        turn("", [call], usage=Usage(1_000, 5, 1_005)),
        turn("## Goal\n检查点", usage=Usage(200, 10, 210)),
    )
    state = RunState()

    compact(chat, state=state)

    call = state.usage_report()["compaction"]["last_call"]
    assert call["path"] == "engine"
    assert call["attempts"] == 2
    assert call["prompt_tokens"] == 1_200
    assert call["completion_tokens"] == 15


# ---- no prefix, no reuse: the old path is the only one that runs ----


def test_without_a_prefix_only_the_engine_prompt_path_runs(spill_root):
    chat = ScriptedChat()

    compact(chat, system=None, tools=None)

    assert len(chat.requests) == 1
    assert chat.requests[0]["system"] == COMPACTION_SYSTEM
    assert chat.requests[0].get("tools") is None


def test_a_partial_prefix_does_not_produce_a_half_reused_request(spill_root):
    """System without the tool table would still miss; a bigger request for nothing is worse."""
    chat = ScriptedChat()

    compact(chat, tools=None)

    assert len(chat.requests) == 1
    assert chat.requests[0]["system"] == COMPACTION_SYSTEM


def test_falls_back_when_there_is_nothing_before_the_cut(spill_root):
    chat = ScriptedChat()

    report = compact(chat, transcript=Transcript(rounds(3)))

    assert report is None
    assert chat.requests == []
