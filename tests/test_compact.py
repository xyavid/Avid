"""Compaction subsystem: the trigger line (reserve semantics), the cut point that never
splits a tool batch, and the single compaction path.

The path keeps the recent N rounds and summarizes older history into one checkpoint message,
created from <original-request>/<conversation> or refreshed from the previous checkpoint,
new material and the repo state.
"""


import shutil
import subprocess

import pytest

from avid.agent import compaction as compaction_module
from avid.agent.compaction import (
    CHECKPOINT_STRUCTURE,
    COMPACTION_SYSTEM,
    CONTEXT_CHAR_LIMIT,
    CREATE_TASK,
    UPDATE_TASK,
    CompactReport,
    ContextBudget,
    _next_spill_path,
    _summarize,
    _summary_message,
    announce,
    checkpoint_of,
    cut_point,
    previous_checkpoint,
    render_conversation,
    repo_state,
    run_compaction,
    spill,
    trigger_chars,
)
from avid.agent.state import RunState
from avid.agent.transcript import Transcript, estimate_chars, validate
from avid.providers.client import LLMError, Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


@pytest.fixture(autouse=True)
def spill_root(tmp_path, monkeypatch):
    """Spills go to a temp workspace so tests do not dirty the repo."""
    from avid.agent.tools import workspace

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


class FakeChat:
    def __init__(self, text="摘要正文"):
        self.text = text
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return Turn(
            message={"role": "assistant", "content": self.text},
            text=self.text,
            tool_calls=[],
            usage=Usage(1, 1, 2),
            model="m",
            finish_reason="stop",
        )


def user(text="hi"):
    return {"role": "user", "content": text}


def assistant(text="", calls=()):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = list(calls)
    return message


def call(call_id="c1", name="read_file"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def bash_call(call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "bash", "arguments": '{"command": "pytest -q"}'},
    }


def tool(call_id="c1", content="结果"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def rounds(count, content="x" * 200):
    """Build count rounds: one assistant with a tool call plus one tool result each."""
    messages = [user("任务")]
    for index in range(count):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", content))
    return messages


# ---- transcript basics (estimate / validate) ----


def test_estimate_counts_content_and_tool_calls():
    plain = [user("abc")]
    with_calls = [assistant("", [call()])]

    assert estimate_chars(plain) >= 3
    assert estimate_chars(with_calls) > len("read_file")


def test_estimate_grows_with_messages():
    assert estimate_chars([user("a")]) < estimate_chars([user("a"), user("b")])


def test_valid_structure_passes():
    assert validate([user(), assistant("", [call()]), tool()]) == []


def test_orphan_tool_result_is_a_violation():
    assert validate([user(), tool()]) != []


# ---- spill channel ----


def test_spill_names_are_unique_per_tag_and_sequence(spill_root):
    first = _next_spill_path(spill_root, "transcript", ".json", tag="runAAAAA")
    second = _next_spill_path(spill_root, "transcript", ".json", tag="runAAAAA")
    other = _next_spill_path(spill_root, "transcript", ".json", tag="runBBBBB")

    assert first != second
    assert "runAAAAA" in str(first) and "runBBBBB" in str(other)


def test_spill_writes_and_returns_workspace_relative_path(spill_root):
    path = spill("x" * 500, "transcript")

    assert path is not None and path.startswith(".avid/context/")
    assert (spill_root / path).read_text(encoding="utf-8") == "x" * 500


# ---- trigger line (reserve semantics) ----


def test_trigger_is_window_minus_reserve_times_measured_rate():
    # window 200k, reserve 16384, measured 0.515 chars/token, system+tools 1500 chars
    trigger = trigger_chars(
        window=200_000,
        prompt_tokens=100_000,
        chars=(1000, 500, 50_000),
        reserve_tokens=16_384,
    )
    per_token = 0.515  # 51_500 / 100_000
    assert trigger == int((200_000 - 16_384) * per_token) - 1500


def test_trigger_without_a_reading_uses_the_default_rate():
    """With a window but no reading, fall back to the conservative 2.0 chars/token default."""
    trigger = trigger_chars(window=200_000, prompt_tokens=None, chars=None, reserve_tokens=16_384)
    assert trigger == (200_000 - 16_384) * 2


def test_trigger_falls_back_without_a_window():
    assert trigger_chars(window=None, prompt_tokens=None, chars=None, reserve_tokens=16_384) == CONTEXT_CHAR_LIMIT


def test_trigger_scales_with_reserve():
    small = trigger_chars(
        window=200_000, prompt_tokens=100_000, chars=(1000, 500, 50_000), reserve_tokens=16_384
    )
    big = trigger_chars(
        window=200_000, prompt_tokens=100_000, chars=(1000, 500, 50_000), reserve_tokens=1_000
    )
    assert big > small  # a larger reserve leaves less room for history


# ---- cut point ----


def test_cut_point_walks_back_full_rounds():
    messages = rounds(12)
    transcript = Transcript(messages)

    cut = cut_point(transcript, keep_recent_turns=10)

    # Before the cut: task message + first 2 rounds; after: the 10 most recent (20 messages)
    assert messages[cut - 1]["role"] == "tool"
    assert messages[cut]["role"] == "assistant"
    assert len(messages) - cut == 20


def test_cut_point_never_splits_a_tool_pair():
    messages = rounds(12)
    transcript = Transcript(messages)

    cut = cut_point(transcript, keep_recent_turns=10)

    assert validate(messages[cut:]) == []


def test_cut_point_returns_zero_when_fewer_rounds_than_the_window():
    transcript = Transcript(rounds(5))

    assert cut_point(transcript, keep_recent_turns=10) == 0


# ---- checkpoint prompt ----


def test_checkpoint_structure_is_the_documented_order():
    """The structure is a contract for the next agent: section names and order are an interface."""
    assert CHECKPOINT_STRUCTURE.splitlines() == [
        "## Goal",
        "## Constraints & Preferences",
        "## Findings",
        "### Verified",
        "### Hypotheses",
        "## Progress",
        "### Done",
        "### In Progress",
        "### Blocked",
        "## Key Decisions",
        "## Next Steps",
        "## Critical Context",
    ]


def test_both_tasks_share_one_structure():
    """Create and update share one skeleton: a second compaction must not change section names."""
    assert CHECKPOINT_STRUCTURE in CREATE_TASK
    assert CHECKPOINT_STRUCTURE in UPDATE_TASK


def test_update_task_keeps_the_update_rules():
    for anchor in (
        "PRESERVE all existing information",
        "In Progress",
        "Done",
        "Next Steps",
        "function names",
        "error messages",
        "no longer relevant",
    ):
        assert anchor in UPDATE_TASK, anchor


def test_no_prompt_may_invent_facts():
    for text in (COMPACTION_SYSTEM, CREATE_TASK, UPDATE_TASK):
        assert "invent" in text


def test_findings_separate_verified_from_hypotheses():
    """Hypotheses must stay labelled: a plain declarative one would be read as fact downstream."""
    assert "## Findings" in CHECKPOINT_STRUCTURE
    assert "### Verified" in CREATE_TASK and "### Hypotheses" in CREATE_TASK
    assert "[hypothesis]" in CREATE_TASK
    assert "[rejected]" in CREATE_TASK
    # The update path must not promote hypotheses to facts either
    assert "[rejected]" in UPDATE_TASK and "Verified" in UPDATE_TASK


def test_engine_prompt_forbids_solving_the_task():
    assert "Do not solve the task." in COMPACTION_SYSTEM
    assert "Do not call tools." in COMPACTION_SYSTEM


def test_render_conversation_keeps_roles_calls_and_tool_names():
    messages = [
        user("把 A 做完"),
        assistant("先跑一遍测试", [bash_call()]),
        tool("c1", "1 passed"),
    ]

    text = render_conversation(messages)

    assert "[user]\n把 A 做完" in text
    assert "[assistant]\n先跑一遍测试" in text
    # Exact command and arguments are copied: dropping them makes the next agent guess
    assert '[tool call] bash({"command": "pytest -q"})' in text
    # Tool results carry the tool name; a call_id alone is unreadable
    assert "[tool result: bash]\n1 passed" in text


def test_render_conversation_marks_images_instead_of_dumping_base64():
    """The summary prompt must never contain base64 — hundreds of KB of useless, token-billed input."""
    from avid.attachments import image_part

    part = image_part(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096, name="shot.png")
    text = render_conversation([{"role": "user", "content": [{"type": "text", "text": "看这张"}, part]}])

    assert "[user]\n看这张\n[图片 shot.png image/png 4KB]" in text
    assert part["data"] not in text


def test_create_prompt_carries_the_request_the_history_and_the_task(spill_root):
    chat = FakeChat()

    run_compaction(
        transcript=Transcript(rounds(12)),
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(),
    )

    request = chat.requests[0]
    body = request["messages"][0]["content"]
    assert request["system"].startswith("You are a context-compaction engine")
    # rounds()'s first message is the original request
    assert "<original-request>\n任务\n</original-request>" in body
    assert "<conversation>" in body and "</conversation>" in body
    assert "<compaction-task>" in body
    assert "<previous-checkpoint>" not in body


def test_create_prompt_asks_for_every_section(spill_root):
    chat = FakeChat()

    run_compaction(
        transcript=Transcript(rounds(12)),
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(),
    )

    body = chat.requests[0]["messages"][0]["content"]
    for section in CHECKPOINT_STRUCTURE.splitlines():
        assert section in body, section


def test_create_prompt_never_probes_the_repo(spill_root, monkeypatch):
    """Only the update path needs the repo state: a first compaction must not shell out to git."""
    monkeypatch.setattr(
        compaction_module, "repo_state", lambda root: pytest.fail("首次压缩不该探仓库")
    )
    chat = FakeChat()

    run_compaction(
        transcript=Transcript(rounds(12)),
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(),
    )

    assert "<repo-state>" not in chat.requests[0]["messages"][0]["content"]


def test_checkpoint_of_reads_back_a_summary_message():
    message = _summary_message("## Goal\nG", ".avid/context/t.json")

    assert checkpoint_of(message) == "## Goal\nG"
    assert checkpoint_of("把这件事做完") is None
    assert checkpoint_of("[历史摘要] 另一套格式") is None


def test_previous_checkpoint_only_recognises_the_transcript_head():
    head = user(_summary_message("## Goal\nG", "p"))

    assert previous_checkpoint([head, user("接着干")]) == "## Goal\nG"
    assert previous_checkpoint([user("接着干")]) is None
    assert previous_checkpoint([]) is None


def compaction_after_more_work(chat, state, limits=None):
    """Compact, work a few more rounds, force a second compaction (the update path), and return
    the transcript and FakeChat: requests[0] is the create, requests[1] the update."""
    limits = limits or budget()
    transcript = Transcript(rounds(12))
    run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=limits
    )
    for index in range(12, 24):
        transcript.append_many(
            [assistant("", [call(f"c{index}")]), tool(f"c{index}", "新结果")]
        )
    chat.text = "## Goal\n把 A 做完（已更新）"
    run_compaction(
        transcript=transcript,
        state=state,
        config=CONFIG,
        chat=chat,
        limits=limits,
        force=True,
    )
    return transcript, chat


def test_second_compaction_updates_the_previous_checkpoint(spill_root, monkeypatch):
    monkeypatch.setattr(
        compaction_module,
        "repo_state",
        lambda root: "$ git status --porcelain -uno\n M avid/agent/compaction.py",
    )
    chat = FakeChat("## Goal\n把 A 做完")

    transcript, chat = compaction_after_more_work(
        chat, RunState(workspace_root=str(spill_root))
    )

    body = chat.requests[1]["messages"][0]["content"]
    assert "<previous-checkpoint>\n## Goal\n把 A 做完\n</previous-checkpoint>" in body
    # The original request is inside the previous checkpoint; feeding it again pulls back
    assert "<original-request>" not in body
    assert "<conversation>" in body and "新结果" in body
    # The previous checkpoint body appears once: no content is fed twice
    assert body.count("把 A 做完") == 1
    assert "<update-task>" in body
    assert "Update the existing structured summary" in body
    # One engine prompt only: the task block selects the mode, the system prompt stays put
    assert chat.requests[1]["system"] == chat.requests[0]["system"]
    # The updated checkpoint returns to the transcript head
    assert "（已更新）" in transcript.as_messages()[0]["content"]


def test_update_prompt_carries_the_repo_state(spill_root, monkeypatch):
    monkeypatch.setattr(
        compaction_module,
        "repo_state",
        lambda root: "$ git status --porcelain -uno\n M avid/agent/compaction.py",
    )
    chat = FakeChat("## Goal\n把 A 做完")

    _transcript, chat = compaction_after_more_work(
        chat, RunState(workspace_root=str(spill_root))
    )

    body = chat.requests[1]["messages"][0]["content"]
    assert "<repo-state>" in body and "M avid/agent/compaction.py" in body


def test_update_prompt_omits_a_missing_repo_state(spill_root, monkeypatch):
    monkeypatch.setattr(compaction_module, "repo_state", lambda root: None)
    chat = FakeChat("## Goal\n把 A 做完")

    _transcript, chat = compaction_after_more_work(
        chat, RunState(workspace_root=str(spill_root))
    )

    assert "<repo-state>" not in chat.requests[1]["messages"][0]["content"]


def test_update_prompt_reports_an_empty_conversation(spill_root, monkeypatch):
    """Two compactions back to back leave no new material: say so instead of an empty block."""
    monkeypatch.setattr(compaction_module, "repo_state", lambda root: None)
    chat = FakeChat("## Goal\n把 A 做完")
    transcript = Transcript(rounds(12))
    state = RunState(workspace_root=str(spill_root))
    limits = budget()

    run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=limits
    )
    run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=limits, force=True
    )

    body = chat.requests[1]["messages"][0]["content"]
    assert (
        "<conversation>\n(no new conversation since the checkpoint)\n</conversation>" in body
    )


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_repo_state_reports_branch_tracked_changes_and_diff_stat(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("环境里没有 git")

    (tmp_path / "a.txt").write_text("one\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    clean = repo_state(tmp_path)

    assert "$ git rev-parse --abbrev-ref HEAD\n" in clean
    assert "$ git status --porcelain -uno\n(none)" in clean

    (tmp_path / "a.txt").write_text("two\n")

    text = repo_state(tmp_path)

    assert "M a.txt" in text
    assert "1 file changed" in text


def test_repo_state_is_silent_outside_a_repo(tmp_path):
    """The probe is best effort: no repo or no git omits the section, never fails compaction."""
    assert repo_state(tmp_path) is None
    assert repo_state(None) is None


# ---- compaction path ----


def budget(**overrides):
    defaults = {"keep_recent_turns": 10, "context_chars": 10, "from_window": False}
    defaults.update(overrides)
    return ContextBudget(**defaults)


def test_below_trigger_nothing_happens(spill_root):
    transcript = Transcript(rounds(12))
    chat = FakeChat()

    report = run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(context_chars=10_000_000),
        force=False,
    )

    assert report is None
    assert chat.requests == []
    assert len(transcript) == len(rounds(12))


def test_above_trigger_summarizes_older_history_and_keeps_recent_turns(spill_root):
    transcript = Transcript(rounds(12))
    before_chars = transcript.estimate_chars()
    chat = FakeChat("之前做了 A，结论 B")

    report = run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(),
        force=False,
    )

    assert report is not None
    messages = transcript.as_messages()
    assert len(messages) == 1 + 20  # one summary + the last 10 rounds
    assert messages[0]["content"].startswith("[历史摘要]")
    assert validate(messages) == []
    # The summarize request carries the compacted older history as tagged text
    assert "x" * 200 in chat.requests[0]["messages"][0]["content"]
    assert transcript.estimate_chars() < before_chars


def test_summarized_history_is_still_a_valid_transcript(spill_root):
    transcript = Transcript(rounds(12))

    run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FakeChat(),
        limits=budget(),
    )

    assert validate(transcript.as_messages()) == []


def test_summarize_failure_keeps_history_and_reports_nothing(spill_root):
    class FailingChat:
        def __call__(self, config, messages, **kwargs):
            raise LLMError("端点挂了")

    transcript = Transcript(rounds(12))

    report = run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FailingChat(),
        limits=budget(),
    )

    assert report is None
    assert transcript.as_messages() == rounds(12)


def test_full_record_is_saved_for_recovery(spill_root):
    transcript = Transcript(rounds(12))

    run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FakeChat(),
        limits=budget(),
    )

    summary_message = transcript.as_messages()[0]["content"]
    recorded = summary_message.split("完整记录：")[1].split("；")[0]
    assert (spill_root / recorded).exists()


def test_auto_path_compacts_at_most_once_per_run(spill_root):
    transcript = Transcript(rounds(12))
    state = RunState(workspace_root=str(spill_root))
    chat = FakeChat()

    first = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget()
    )
    second = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget()
    )

    assert first is not None
    assert second is None  # once per run: prevents a retry storm after a failed summary
    assert state.compacted is True


def test_force_bypasses_threshold_and_the_once_guard(spill_root):
    transcript = Transcript(rounds(12))
    state = RunState(workspace_root=str(spill_root))
    chat = FakeChat()

    first = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget(), force=True
    )
    second = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget(), force=True
    )

    assert first is not None and second is not None  # force bypasses the guard


def test_cursor_hook_receives_summary_and_kept_count(spill_root):
    covered: list = []
    transcript = Transcript(rounds(12))

    run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FakeChat(),
        limits=budget(),
        on_compaction=lambda summary, keep: covered.append((summary, keep)),
    )

    assert len(covered) == 1
    summary, keep = covered[0]
    assert summary["content"].startswith("[历史摘要]")
    assert keep == 20  # 10 rounds = 20 messages


def test_announce_records_ledger_and_event():
    state = RunState()
    events: list = []
    state.observer = events.append

    announce(
        CompactReport("compact_history", "摘要更早 12 条", 33, 1), state
    )

    assert state.compactions == 1
    assert events[0].type == "context_compacted"
    assert events[0].data["step"] == "compact_history"


def test_summary_call_is_not_capped(spill_root):
    """The summary is a model call too: a fixed cap is spent on reasoning, the summary comes
    back empty and this step silently stops working."""

    class LongChat:
        def __call__(self, config, messages, **kwargs):
            assert "max_tokens" not in kwargs or kwargs["max_tokens"] is None
            return Turn(
                message={"role": "assistant", "content": "这是摘要"},
                text="这是摘要",
                tool_calls=[],
                usage=Usage(1, 1, 2),
                model="m",
                finish_reason="stop",
            )

    assert (
        _summarize(
            "素材文本", config=CONFIG, chat=LongChat(), system=COMPACTION_SYSTEM
        )
        == "这是摘要"
    )


def test_summary_message_carries_the_recovery_path():
    text = _summary_message("要点", ".avid/context/transcript-0001.json")

    assert text.startswith("[历史摘要]")
    assert "完整记录：.avid/context/transcript-0001.json" in text
