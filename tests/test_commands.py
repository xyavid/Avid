"""In-session commands: parse rules and skill text; both command bodies are the caller's wiring.

`/compact` is exercised where it now lives — `ContextManager.compact_now` in test_context.py.
"""

from __future__ import annotations

from avid.agent import commands

# ---------- parse rules ----------


def test_paths_and_plain_text_are_not_commands():
    for text in ("/home/fishy/x", "读 /etc/hosts 看看", "你好", "/", "/Home", "/a b", "//compact"):
        assert commands.match_command(text, skill_names=["pdf"]) is None, text


def test_registered_command_matches():
    match = commands.match_command("/compact")

    assert match is not None
    assert match.kind == commands.KIND_COMMAND
    assert match.name == "compact"


def test_rewind_is_a_registered_command():
    match = commands.match_command("/rewind")

    assert "rewind" in commands.COMMANDS
    assert match is not None
    assert match.kind == commands.KIND_COMMAND
    assert match.name == "rewind"


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

    assert "/compact" in text and "/rewind" in text and "/pdf" in text


# ---------- skill text ----------


def test_skill_text_reads_the_workspace_skill(tmp_path):
    (tmp_path / "skills" / "pdf").mkdir(parents=True)
    (tmp_path / "skills" / "pdf" / "SKILL.md").write_text(
        "---\ndescription: 处理 PDF\n---\n使用 pdf 的步骤", encoding="utf-8"
    )

    body = commands.skill_text("pdf", workspace_root=str(tmp_path))

    assert body is not None and "使用 pdf 的步骤" in body
    assert commands.skill_text("never-exists", workspace_root=str(tmp_path)) is None
