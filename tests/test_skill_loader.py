from pathlib import Path

from avid import skill_loader
from avid.skill_loader import AGENT_INSTRUCTIONS, SkillLoader

REPO_SKILLS = Path(__file__).resolve().parent.parent / "skills"


def write_skill(root: Path, directory: str, text: str) -> None:
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")


# ---------- scan ----------


def test_scans_frontmatter(tmp_path):
    text = "---\nname: code-review\ndescription: 做代码审查\n---\n正文\n"
    write_skill(tmp_path, "code-review", text)

    loader = SkillLoader(tmp_path).scan()

    assert loader.skills["code-review"] == {
        "name": "code-review",
        "description": "做代码审查",
        "content": text,
    }


def test_name_defaults_to_the_parent_directory(tmp_path):
    write_skill(tmp_path, "pdf", "---\ndescription: 处理 PDF\n---\n正文\n")

    assert set(SkillLoader(tmp_path).scan().skills) == {"pdf"}


def test_description_defaults_to_the_first_line_without_hashes(tmp_path):
    write_skill(tmp_path, "x", "## 第一行说明\n\n后面的内容\n")

    assert SkillLoader(tmp_path).scan().skills["x"]["description"] == "第一行说明"


def test_description_skips_leading_blank_lines(tmp_path):
    write_skill(tmp_path, "x", "\n\n   \n### 真正的第一行\n正文\n")

    assert SkillLoader(tmp_path).scan().skills["x"]["description"] == "真正的第一行"


def test_frontmatter_wins_over_defaults(tmp_path):
    write_skill(
        tmp_path,
        "dir-name",
        "---\nname: real-name\ndescription: 真描述\n---\n# 正文标题\n",
    )

    loader = SkillLoader(tmp_path).scan()

    assert set(loader.skills) == {"real-name"}
    assert loader.skills["real-name"]["description"] == "真描述"


def test_scan_clears_the_registry_first(tmp_path):
    write_skill(tmp_path, "a", "---\ndescription: A\n---\n")
    loader = SkillLoader(tmp_path).scan()
    assert set(loader.skills) == {"a"}

    (tmp_path / "a" / "SKILL.md").unlink()
    loader.scan()

    assert loader.skills == {}


def test_skips_entries_that_are_not_files(tmp_path):
    (tmp_path / "weird").mkdir()
    (tmp_path / "weird" / "SKILL.md").mkdir()  # 目录，不是文件
    write_skill(tmp_path, "good", "---\ndescription: 好的\n---\n")

    assert set(SkillLoader(tmp_path).scan().skills) == {"good"}


def test_skips_entries_resolving_outside_the_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\ndescription: 外面\n---\n", encoding="utf-8")

    root = tmp_path / "skills"
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    assert SkillLoader(root).scan().skills == {}


def test_missing_directory_is_not_an_error(tmp_path):
    loader = SkillLoader(tmp_path / "nope").scan()

    assert loader.skills == {}
    assert loader.catalog() == ""


# ---------- catalog ----------


def test_catalog_lists_name_and_description_sorted(tmp_path):
    write_skill(tmp_path, "b", "---\nname: beta\ndescription: 第二个\n---\n")
    write_skill(tmp_path, "a", "---\nname: alpha\ndescription: 第一个\n---\n")

    assert SkillLoader(tmp_path).scan().catalog() == "- alpha: 第一个\n- beta: 第二个"


# ---------- build_system_prompt ----------


def test_system_prompt_contains_the_required_parts(tmp_path, monkeypatch):
    from avid.tools import workspace

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "code-review", "---\ndescription: 做代码审查\n---\n")

    prompt = SkillLoader(skills_root).scan().build_system_prompt()

    assert str(tmp_path) in prompt  # WORKDIR
    assert "Act, don't explain." in prompt
    assert "todo_write" in prompt  # 固定 Agent 指令
    assert "- code-review: 做代码审查" in prompt
    assert "Use load_skill to read the full instructions when a skill applies." in prompt


def test_system_prompt_follows_directory_changes(tmp_path):
    """验收项：技能目录变化后 system prompt 同步更新。"""
    loader = SkillLoader(tmp_path)
    assert "code-review" not in loader.scan().build_system_prompt()

    write_skill(tmp_path, "code-review", "---\ndescription: 做代码审查\n---\n")

    assert "code-review" in loader.scan().build_system_prompt()


def test_system_prompt_accepts_custom_instructions(tmp_path):
    prompt = SkillLoader(tmp_path).scan().build_system_prompt("你是 subagent，只做一件事。")

    assert prompt.startswith("你是 subagent，只做一件事。")
    assert "Act, don't explain." in prompt


def test_system_prompt_says_so_when_there_are_no_skills(tmp_path):
    assert "（当前没有可用技能）" in SkillLoader(tmp_path).scan().build_system_prompt()


def test_default_instructions_ask_for_a_plan_first():
    assert "todo_write" in AGENT_INSTRUCTIONS


# ---------- load ----------


def test_load_returns_the_full_file(tmp_path):
    text = "---\ndescription: 做代码审查\n---\n\n# 步骤\n1. 先看规格\n"
    write_skill(tmp_path, "code-review", text)

    assert SkillLoader(tmp_path).scan().load("code-review") == text


def test_load_miss_lists_available_names(tmp_path):
    write_skill(tmp_path, "alpha", "---\ndescription: A\n---\n")
    write_skill(tmp_path, "beta", "---\ndescription: B\n---\n")

    result = SkillLoader(tmp_path).scan().load("nope")

    assert result == "Error: Unknown skill 'nope'. Available: alpha, beta"


def test_load_miss_on_an_empty_registry_says_none(tmp_path):
    result = SkillLoader(tmp_path).scan().load("nope")

    assert result == "Error: Unknown skill 'nope'. Available: none"


def test_load_treats_the_name_as_a_key_not_a_path(tmp_path):
    write_skill(tmp_path, "alpha", "---\ndescription: A\n---\n秘密内容")
    loader = SkillLoader(tmp_path).scan()

    assert loader.load("../../etc/passwd").startswith("Error: Unknown skill")
    assert loader.load("alpha/SKILL.md").startswith("Error: Unknown skill")


# ---------- 运行隔离：每个 RunState 自带一份注册表 ----------


def test_two_loaders_do_not_share_state(tmp_path):
    write_skill(tmp_path, "a", "---\ndescription: A\n---\n")

    first = SkillLoader(tmp_path).scan()
    second = SkillLoader(tmp_path)

    assert set(first.skills) == {"a"}
    assert second.skills == {}


def test_each_scan_produces_its_own_registry(tmp_path):
    write_skill(tmp_path, "a", "---\ndescription: A\n---\n")

    first = SkillLoader(tmp_path).scan()
    second = SkillLoader(tmp_path).scan()

    assert first.skills == second.skills
    assert first.skills is not second.skills


# ---------- 仓库里真实的技能 ----------


def test_repository_skills_are_scanned():
    loader = SkillLoader(REPO_SKILLS).scan()

    assert set(loader.skills) == {"agent-builder", "code-review", "pdf"}
    for name, skill in loader.skills.items():
        assert skill["description"], name
        assert skill["content"].strip(), name


def test_repository_catalog_shape():
    lines = SkillLoader(REPO_SKILLS).scan().catalog().splitlines()

    assert len(lines) == 3
    assert all(line.startswith("- ") and ": " in line for line in lines)


def test_default_skills_dir_sits_at_the_repository_root(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_loader, "SKILLS_DIR", tmp_path)

    assert SkillLoader().skills_dir == tmp_path
