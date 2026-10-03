"""会话内命令：解析规则、技能文本、/compact 执行体。"""

from __future__ import annotations

from avid.agent import commands
from avid.providers.client import Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


class Summarizer:
    def __init__(self, text="要点摘要"):
        self.calls = 0
        self.text = text

    def __call__(self, config, messages, **kwargs):
        self.calls += 1
        return Turn(
            message={"role": "assistant", "content": self.text},
            text=self.text,
            tool_calls=[],
            usage=Usage(1, 1, 2),
            model="m",
            finish_reason="stop",
        )


def rounds(count):
    messages = [{"role": "user", "content": "任务"}]
    for index in range(count):
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{index}", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}
        ]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "x" * 200})
    return messages


# ---------- 解析规则 ----------


def test_paths_and_plain_text_are_not_commands():
    for text in ("/home/fishy/x", "读 /etc/hosts 看看", "你好", "/", "/Home", "/a b", "//compact"):
        assert commands.match_command(text, skill_names=["pdf"]) is None, text


def test_registered_command_matches():
    match = commands.match_command("/compact")

    assert match is not None
    assert match.kind == commands.KIND_COMMAND
    assert match.name == "compact"


def test_skill_name_matches_before_unknown():
    match = commands.match_command("/pdf", skill_names=["pdf", "code-review"])

    assert match is not None
    assert match.kind == commands.KIND_SKILL


def test_unknown_single_token_is_flagged():
    match = commands.match_command("/pdff", skill_names=["pdf"])

    assert match is not None
    assert match.kind == commands.KIND_UNKNOWN


def test_help_text_lists_commands_and_skills(tmp_path):
    (tmp_path / "skills" / "pdf").mkdir(parents=True)
    (tmp_path / "skills" / "pdf" / "SKILL.md").write_text(
        "---\ndescription: 处理 PDF\n---\n正文", encoding="utf-8"
    )

    text = commands.help_text(workspace_root=str(tmp_path))

    assert "/compact" in text and "/pdf" in text


# ---------- 技能文本 ----------


def test_skill_text_reads_the_workspace_skill(tmp_path):
    (tmp_path / "skills" / "pdf").mkdir(parents=True)
    (tmp_path / "skills" / "pdf" / "SKILL.md").write_text(
        "---\ndescription: 处理 PDF\n---\n使用 pdf 的步骤", encoding="utf-8"
    )

    body = commands.skill_text("pdf", workspace_root=str(tmp_path))

    assert body is not None and "使用 pdf 的步骤" in body
    assert commands.skill_text("never-exists", workspace_root=str(tmp_path)) is None


# ---------- /compact 执行体 ----------


def test_compact_session_forces_and_persists(tmp_path):
    summarizer = Summarizer()
    covered: list = []

    report = commands.compact_session(
        history=rounds(12),
        config=CONFIG,
        summarize=summarizer,
        workspace_root=str(tmp_path),
        on_compaction=lambda summary, keep: covered.append((summary, keep)),
    )

    assert report is not None and report.step == "compact_history"
    assert summarizer.calls == 1
    assert len(covered) == 1
    summary, keep = covered[0]
    assert summary["content"].startswith("[历史摘要]")
    assert keep == 20  # 默认保留最近 10 轮


def test_compact_session_with_short_history_does_nothing():
    summarizer = Summarizer()

    report = commands.compact_session(
        history=rounds(1),
        config=CONFIG,
        summarize=summarizer,
        workspace_root=None,
    )

    assert report is None
    assert summarizer.calls == 0
