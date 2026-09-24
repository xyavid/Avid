"""四级 deny 阶梯：ADMIN / SYSTEM / PROJECT / USER，以及"下层不许抵消上层"。"""

from __future__ import annotations

from pathlib import Path

import pytest

from avid.policy.permission import (
    OPERATION_READ,
    OPERATION_WRITE,
    TIER_ADMIN,
    TIER_PROJECT,
    TIER_SYSTEM,
    TIERS,
    VERDICT_DENY,
    Ladder,
    PolicyConfigError,
)


def write_policy(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """一个假的"宿主"：家目录与系统策略文件都在 tmp 下。"""
    home = tmp_path / "host-home"
    home.mkdir()
    return home


def ladder_for(tmp_path: Path, host: Path, *, system: str | None = None, project: str | None = None):
    system_file = write_policy(host / ".avid" / "policy.toml", system) if system else None
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    project_file = write_policy(root / ".avid" / "policy.toml", project) if project else None
    return root, Ladder.load(
        root=root,
        home=host,
        system_path=system_file or (host / ".avid" / "policy.toml"),
        project_path=project_file or (root / ".avid" / "policy.toml"),
    )


# ---------------------------------------------------------------- 层级顺序


def test_tier_order_is_the_declared_ladder():
    assert TIERS == (TIER_ADMIN, TIER_SYSTEM, TIER_PROJECT, "user")


def test_admin_rules_cover_the_credential_list(host):
    """ADMIN 档的表逐条对账：漏一条就是"凭据可以被读"。"""
    from avid.policy.permission import ADMIN_WRITABLE_SOURCES  # noqa: F401  (表在 rules 里)
    from avid.policy.rules import ADMIN_PATH_RULES

    paths = {path for path, _operations, _reason in ADMIN_PATH_RULES}
    assert {
        "~/.ssh",
        "~/.aws",
        "~/.gnupg",
        "~/.docker/config.json",
        "~/.netrc",
        "~/.git-credentials",
        "/etc/shadow",
        "/etc/sudoers",
        "/root",
        "~/.bashrc",
        "~/.avid",
    } <= paths


def test_dot_ssh_is_denied_for_read_and_write(host, tmp_path):
    _root, ladder = ladder_for(tmp_path, host)
    for operation in (OPERATION_READ, OPERATION_WRITE):
        rule = ladder.check(host / ".ssh" / "id_rsa", (operation,))
        assert rule is not None
        assert (rule.tier, rule.verdict) == (TIER_ADMIN, VERDICT_DENY)
        assert rule.relaxable is False


def test_admin_rules_are_not_relaxable_by_the_host_user(host, tmp_path):
    """宿主可以放开**内置的项目默认**，但放不开 ADMIN（凭据永远不给）。"""
    root, ladder = ladder_for(
        tmp_path,
        host,
        system="""
[allow]
read = ["~/.ssh"]
write = ["~/.ssh"]
""",
    )
    assert ladder.check(host / ".ssh" / "id_rsa", (OPERATION_READ,)) is not None


def test_system_config_adds_an_higher_tier_deny(host, tmp_path):
    _root, ladder = ladder_for(
        tmp_path,
        host,
        system="""
[deny]
write = ["~/work/secret/**"]
""",
    )
    rule = ladder.check(host / "work" / "secret" / "a.txt", (OPERATION_WRITE,))
    assert rule is not None and rule.tier == TIER_SYSTEM
    assert ladder.check(host / "work" / "public" / "a.txt", (OPERATION_WRITE,)) is None


def test_project_config_can_only_add_denies(host, tmp_path):
    root, ladder = ladder_for(
        tmp_path,
        host,
        project="""
[deny]
read = ["**/private/**"]

[allow]
write = [".github/workflows"]
""",
    )
    # deny 生效
    assert ladder.check(root / "x" / "private" / "k.pem", (OPERATION_READ,)) is not None
    # [allow] 被忽略，并留一条能读到的 note
    assert any("allow" in note and "仓库不能削弱" in note for note in ladder.notes)
    assert ladder.check(root / ".github" / "workflows" / "ci.yml", (OPERATION_WRITE,)) is not None


def test_system_allow_is_the_only_way_to_relax_a_project_default(host, tmp_path):
    """``.github/workflows`` 是**可放松的内置默认**：宿主写一句话就放开。"""
    root, strict = ladder_for(tmp_path, host)
    assert strict.verdict_for(root / ".github" / "workflows" / "ci.yml", ("write",)) is not None

    root, relaxed = ladder_for(
        tmp_path,
        host,
        system="""
[allow]
write = [".github/workflows"]
""",
    )
    assert relaxed.verdict_for(root / ".github" / "workflows" / "ci.yml", ("write",)) is None
    # 别的内置默认没被顺带放开
    assert relaxed.verdict_for(root / ".git" / "hooks" / "pre-commit", ("write",)) is not None


def test_deny_beats_ask_even_when_ask_is_at_a_higher_tier():
    """deny 全局高于 ask：PROJECT 的 deny 不会被 SYSTEM 的 ask 挤掉。

    阶梯顺序只决定"同为 deny 时谁更硬"；deny 与 ask 之间是**另一种**优先级——
    原则③的另一半。这里直接造两条规则来钉住它，而不是指望内置表恰好凑出这个组合。
    """
    from avid.policy.rules import Ladder, Rule

    ask = Rule(
        tier=TIER_SYSTEM,
        path="/ws/secrets",
        operations=frozenset({"read"}),
        verdict="ask",
        reason="宿主说先问一句",
    )
    deny = Rule(
        tier=TIER_PROJECT,
        path="/ws/secrets",
        operations=frozenset({"read"}),
        verdict=VERDICT_DENY,
        reason="仓库说不许",
    )
    ladder = Ladder(rules=(ask, deny), root="/ws")

    rule = ladder.check("/ws/secrets/a.txt", ("read",))
    assert rule is not None
    assert (rule.verdict, rule.tier) == (VERDICT_DENY, TIER_PROJECT)


def test_builtin_project_defaults_are_deny_or_ask():
    from avid.policy.rules import PROJECT_PATH_RULES

    table = {path: verdict for path, _ops, _reason, verdict in PROJECT_PATH_RULES}
    assert table[".git/hooks"] == VERDICT_DENY
    assert table[".git/config"] == VERDICT_DENY
    assert table[".github/workflows"] == VERDICT_DENY
    assert table[".env"] == "ask" and table[".env.*"] == "ask"


# ---------------------------------------------------------------- 匹配口径


def test_pattern_semantics(tmp_path):
    from avid.policy.rules import path_hit

    # `**/` 表示"零层或多层目录"：gitignore 习惯写下来的 deny 不能静默失效
    assert path_hit("/ws/private/x", "/ws/**/private/**")
    assert path_hit("/ws/a/b/private/x", "/ws/**/private/**")
    # 没有通配符时是目录语义
    assert path_hit("/ws/.git/hooks", "/ws/.git/hooks")
    assert path_hit("/ws/.git/hooks/pre-commit", "/ws/.git/hooks")
    assert not path_hit("/ws/.git/objects/x", "/ws/.git/hooks")
    # `*` 跨 `/`（比 gitignore 更严）
    assert path_hit("/ws/a/b/c.txt", "/ws/*.txt")


def test_relative_patterns_resolve_against_the_workspace_root(host, tmp_path):
    root, ladder = ladder_for(tmp_path, host)
    rule = ladder.check(root / ".env", (OPERATION_READ,))
    assert rule is not None and rule.path == ".env"
    # 另一个工作区里的同名文件不该被这条相对规则命中
    other = tmp_path / "other"
    other.mkdir()
    assert ladder.check(other / ".env", (OPERATION_READ,)) is None


def test_tilde_expands_against_the_injected_home(host, tmp_path):
    """``~`` 必须按**注入的**宿主家目录展开：否则"策略指向哪台机器"取决于跑进程的用户。"""
    _root, ladder = ladder_for(tmp_path, host)
    assert ladder.check(host / ".ssh" / "id_rsa", (OPERATION_READ,)) is not None
    assert ladder.check(Path.home() / ".ssh" / "id_rsa", (OPERATION_READ,)) is None


# ---------------------------------------------------------------- 失败模型


def test_broken_system_policy_refuses_to_start(host, tmp_path):
    system = write_policy(host / ".avid" / "policy.toml", "this is not toml = =")
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(PolicyConfigError):
        Ladder.load(root=root, home=host, system_path=system)


def test_broken_project_policy_is_ignored_with_a_note(host, tmp_path):
    """仓库文件损坏不能拦住运行：它只能加严，忽略它不会变宽。"""
    root, ladder = ladder_for(tmp_path, host, project="not toml at all =")
    assert any("已忽略" in note for note in ladder.notes)
    assert ladder.check(root / ".env", (OPERATION_READ,)) is not None  # 内置默认仍在


def test_unknown_sections_and_keys_are_rejected(host, tmp_path):
    system = write_policy(host / ".avid" / "policy.toml", "[nope]\nwrite = []\n")
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(PolicyConfigError):
        Ladder.load(root=root, home=host, system_path=system)


def test_project_schema_errors_are_only_noted(host, tmp_path):
    root, ladder = ladder_for(tmp_path, host, project="[deny]\nexecute = []\n")
    assert any("不合 schema" in note for note in ladder.notes)


def test_describe_exposes_the_flat_view(host, tmp_path):
    _root, ladder = ladder_for(tmp_path, host)
    rows = ladder.describe()
    assert {"tier", "path", "operations", "verdict", "reason", "source"} <= set(rows[0])
    assert any(row["tier"] == TIER_ADMIN for row in rows)
