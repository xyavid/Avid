"""压缩子系统测试：触发线（reserve 语义）、切点（安全边界）、单一压缩通路。

修复前这里是五步阶梯（tool_result_budget / snip / micro / compact_history /
reactive）各自的用例；按用户裁定收敛为一条 Pi 式通路——保留最近 N 轮完整
历史，更早部分经一次 summarize 压成一条检查点消息，切点绝不落在工具批中间。

检查点分两份 prompt：首次压缩按 <original-request>/<conversation> 写新检查点；
transcript 头部已是检查点时走更新路径，拿上一份 + 新素材 + 仓库现状刷新它。
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
    """落盘写到临时工作区，测试不污染仓库。"""
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
    """构造 count 轮：每轮一条 assistant（带工具调用）加一条工具结果。"""
    messages = [user("任务")]
    for index in range(count):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", content))
    return messages


# ---------- Transcript 基础（estimate / validate） ----------


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


# ---------- 落盘通道 ----------


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


# ---------- 触发线（reserve 语义） ----------


def test_trigger_is_window_minus_reserve_times_measured_rate():
    # window 200k，reserve 16384，实测 0.515 字符/token，system+tools 1500 字符
    trigger = trigger_chars(
        window=200_000,
        prompt_tokens=100_000,
        chars=(1000, 500, 50_000),
        reserve_tokens=16_384,
    )
    per_token = 0.515  # 51_500 / 100_000
    assert trigger == int((200_000 - 16_384) * per_token) - 1500


def test_trigger_without_a_reading_uses_the_default_rate():
    """有窗口但没有读数：按 2.0 字符/token 的保守默认比率折算，而不是整个放弃派生。"""
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
    assert big > small  # reserve 越大，留给历史的空间越小


# ---------- 切点 ----------


def test_cut_point_walks_back_full_rounds():
    messages = rounds(12)
    transcript = Transcript(messages)

    cut = cut_point(transcript, keep_recent_turns=10)

    # 切点前是任务消息 + 前 2 轮；切点后保留最近 10 轮（20 条消息）
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


# ---------- 检查点 prompt ----------


def test_checkpoint_structure_is_the_documented_order():
    """结构是给下一个 agent 读的契约：段名与顺序都是接口，不是排版。"""
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
    """创建与更新写同一份骨架：第二次压缩不能把检查点换成另一套段名。"""
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
    """假设必须带标签：写成陈述句的假设，下一个 agent 会当事实用。"""
    assert "## Findings" in CHECKPOINT_STRUCTURE
    assert "### Verified" in CREATE_TASK and "### Hypotheses" in CREATE_TASK
    assert "[hypothesis]" in CREATE_TASK
    assert "[rejected]" in CREATE_TASK
    # 更新路径同样不许把假设升格成事实
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
    # 精确命令与参数照抄：检查点里丢掉参数，下一个 agent 就得重猜
    assert '[tool call] bash({"command": "pytest -q"})' in text
    # 工具结果要标出是哪个工具的结果，光有 call_id 读不出来
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
    # rounds() 的首条消息就是最初请求
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
    """仓库现状只有更新路径要：首次压缩多跑一次 git 是白花。"""
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
    """先压一次，再干几轮，然后强制压第二次——第二次就是更新路径。

    返回 transcript 与那个 FakeChat：requests[0] 是首次压缩，requests[1] 是更新。
    """
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
    # 最初的请求已经并进上一份检查点，再喂一遍会把方向拉回去
    assert "<original-request>" not in body
    assert "<conversation>" in body and "新结果" in body
    # 上一份的内容只出现一次：同一段内容不喂两遍（骨架里也有 ## Goal，按正文断言）
    assert body.count("把 A 做完") == 1
    assert "<update-task>" in body
    assert "Update the existing structured summary" in body
    # 引擎提示只有一份：模式由任务块区分，系统提示不跟着模式漂
    assert chat.requests[1]["system"] == chat.requests[0]["system"]
    # 更新后的检查点回到 transcript 头部
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
    """连着压两次时新素材是空的：明说，别让模型对着空块猜该更新什么。"""
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
    """探针是锦上添花：不是仓库、没有 git 都只是没有这一段，不该让压缩失败。"""
    assert repo_state(tmp_path) is None
    assert repo_state(None) is None


# ---------- 压缩通路 ----------


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
    assert len(messages) == 1 + 20  # 摘要一条 + 最近 10 轮
    assert messages[0]["content"].startswith("[历史摘要]")
    assert validate(messages) == []
    # 摘要请求带上了被压缩的更早历史（渲染成带标签的文本，见下面的 prompt 用例）
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
    assert second is None  # 每运行一次：防摘要失败后的逐轮重试风暴
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

    assert first is not None and second is not None  # force 不受守护限制


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
    assert keep == 20  # 最近 10 轮 = 20 条消息


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
    """摘要也是模型调用：写死的上限会被推理吃光，摘要变空 → 这一步静默失效。"""

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
