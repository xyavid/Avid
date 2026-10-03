"""Four-tier deny ladder in which a lower-tier allow never cancels a higher-tier deny."""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("avid.policy.rules")

# Ladder tiers; this order defines priority, so a new tier must be inserted in the right place.
TIER_ADMIN = "admin"
TIER_SYSTEM = "system"
TIER_PROJECT = "project"
TIER_USER = "user"
TIERS: tuple[str, ...] = (TIER_ADMIN, TIER_SYSTEM, TIER_PROJECT, TIER_USER)
TIER_ORDER: dict[str, int] = {tier: index for index, tier in enumerate(TIERS)}

# Tier verdicts: deny is final, while ask routes a matching target to review instead of refusing it.
VERDICT_DENY = "deny"
VERDICT_ASK = "ask"

# Operation names a rule can govern: reads, writes, or both.
OPERATION_READ = "read"
OPERATION_WRITE = "write"
OPERATIONS: tuple[str, ...] = (OPERATION_READ, OPERATION_WRITE)

# The same relative name at two different roots: the host home and the workspace.
SYSTEM_POLICY_RELPATH = Path(".avid") / "policy.toml"
PROJECT_POLICY_RELPATH = Path(".avid") / "policy.toml"
# Explicit override for tests and multi-host runs.
SYSTEM_POLICY_ENV = "AVID_POLICY_FILE"


class PolicyConfigError(ValueError):
    """The host security policy cannot be read; the failure direction is refusing to start."""


def expand_pattern(
    pattern: str, *, root: str | Path | None = None, home: str | Path | None = None
) -> str:
    """Expands a pattern to absolute form; relative patterns resolve against the workspace root.

    ``home`` is injected so tests and multi-host runs can replace the whole host.
    """
    text = os.path.expandvars(pattern)
    if text.startswith("~"):
        base_home = str(home) if home is not None else str(Path.home())
        text = base_home + text[1:]
    if Path(text).is_absolute():
        return text
    base = Path(root).resolve() if root is not None else Path.cwd()
    return str(base / text)


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compiles a pattern where ``**/`` spans zero or more directories and ``*`` crosses ``/``."""
    out: list[str] = []
    index = 0
    # Hand-translated because fnmatch's **/ needs one directory, which would silently drop a deny.
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append(".*")
            index += 1
            continue
        if char == "?":
            out.append(".")
            index += 1
            continue
        out.append(re.escape(char))
        index += 1

    body = "".join(out)
    if any(mark in pattern for mark in "*?"):
        return re.compile("^" + body + "$")
    # A pattern without wildcards also covers everything below the named directory.
    return re.compile("^" + body + r"(?:/.*)?$")


def path_hit(text: str, pattern: str) -> bool:
    """Reports whether ``text`` is matched by ``pattern`` under the rules of the compiler."""
    return _compile_pattern(pattern).match(text) is not None


@dataclass(frozen=True)
class Rule:
    """One path rule, with the operations it governs and whether a SYSTEM allow can relax it."""

    tier: str
    path: str
    operations: frozenset[str]
    verdict: str
    reason: str
    source: str = "builtin"
    relaxable: bool = False

    def matches(
        self,
        target: str | Path,
        *,
        root: str | Path | None = None,
        home: str | Path | None = None,
    ) -> bool:
        return path_hit(str(target), expand_pattern(self.path, root=root, home=home))


@dataclass(frozen=True)
class Ladder:
    """Assembled ladder; ``rules`` is already ordered by tier and ``check`` returns the top hit."""

    rules: tuple[Rule, ...] = ()
    relaxations: tuple[Rule, ...] = ()
    notes: tuple[str, ...] = ()
    root: str | None = None
    home: str | None = None

    def check(
        self, target: str | Path, operations: tuple[str, ...] | frozenset[str]
    ) -> Rule | None:
        """Returns the highest-priority hit: deny outranks ask, then the higher tier wins."""
        wanted = set(operations) or set(OPERATIONS)
        hits = [
            rule
            for rule in self.rules
            if rule.operations & wanted
            and rule.matches(target, root=self.root, home=self.home)
        ]
        if not hits:
            return None
        # Deny is compared before the tier, so a PROJECT deny still beats a SYSTEM ask.
        hits.sort(key=lambda rule: (rule.verdict != VERDICT_DENY, TIER_ORDER[rule.tier]))
        return hits[0]

    def relaxed(self, rule: Rule) -> bool:
        """Reports whether a SYSTEM allow relaxes this rule; a locked tier always returns False."""
        if not rule.relaxable:
            return False
        here = expand_pattern(rule.path, root=self.root, home=self.home)
        return any(
            item.operations & rule.operations
            and path_hit(here, expand_pattern(item.path, root=self.root, home=self.home))
            for item in self.relaxations
        )

    def verdict_for(
        self, target: str | Path, operations: tuple[str, ...] | frozenset[str]
    ) -> Rule | None:
        """Returns the hit with any rule relaxed by a SYSTEM allow filtered out."""
        rule = self.check(target, operations)
        if rule is not None and self.relaxed(rule):
            logger.info("规则 %s 被 SYSTEM ALLOW 放开：%s", rule.path, rule.reason)
            return None
        return rule

    @classmethod
    def load(
        cls,
        *,
        root: str | Path | None = None,
        home: str | Path | None = None,
        system_path: str | Path | None = None,
        project_path: str | Path | None = None,
    ) -> "Ladder":
        """Assembles the tiers; a broken SYSTEM file raises, a broken PROJECT one is ignored."""
        home_dir = Path(home) if home is not None else Path.home()
        root_text = str(Path(root).resolve()) if root is not None else None
        notes: list[str] = []

        rules: list[Rule] = list(builtin_rules(root=root_text))
        relaxations: list[Rule] = []

        system_file = Path(system_path) if system_path else _system_policy_path(home_dir)
        if system_file.is_file():
            parsed = _read_policy(system_file, strict=True)
            assert parsed is not None  # strict mode returns a table or raises PolicyConfigError
            rules.extend(_rules_from(parsed, tier=TIER_SYSTEM, source="system_config"))
            relaxations.extend(_allow_from(parsed))

        project_file = (
            Path(project_path)
            if project_path
            else (Path(root_text) / PROJECT_POLICY_RELPATH if root_text else None)
        )
        if project_file is not None and project_file.is_file():
            parsed = _read_policy(project_file, strict=False)
            if parsed is None:
                notes.append(f"{project_file} 读不出来或不合 schema，已忽略（它只能加严）")
            else:
                # A repository file may not weaken host policy, so its [allow] section is dropped.
                if "allow" in parsed:
                    notes.append(
                        f"{project_file} 的 [allow] 已忽略：仓库不能削弱宿主机安全策略"
                    )
                rules.extend(
                    _rules_from(parsed, tier=TIER_PROJECT, source="project_config")
                )

        return cls(
            rules=tuple(rules),
            relaxations=tuple(relaxations),
            notes=tuple(notes),
            root=root_text,
            home=str(home_dir),
        )

    def describe(self) -> list[dict[str, str]]:
        """Returns a flat view of the assembled rules for diagnostics and tests."""
        return [
            {
                "tier": rule.tier,
                "path": rule.path,
                "operations": ",".join(sorted(rule.operations)),
                "verdict": rule.verdict,
                "reason": rule.reason,
                "source": rule.source,
            }
            for rule in self.rules
        ]


def _system_policy_path(home: Path) -> Path:
    """Returns the host policy file: AVID_POLICY_FILE, then AVID_HOME, then the home directory."""
    override = os.environ.get(SYSTEM_POLICY_ENV)
    if override:
        return Path(override).expanduser()
    if os.environ.get("AVID_HOME"):
        from .userdirs import avid_home

        return avid_home() / "policy.toml"
    return home / SYSTEM_POLICY_RELPATH


# Admin tier: credentials and host policy itself; reads and writes are denied and never relaxable.
ADMIN_PATH_RULES: tuple[tuple[str, frozenset[str], str], ...] = (
    ("~/.ssh", frozenset(OPERATIONS), "SSH 私钥与配置"),
    ("~/.aws", frozenset(OPERATIONS), "云凭据"),
    ("~/.gnupg", frozenset(OPERATIONS), "GPG 私钥"),
    ("~/.docker/config.json", frozenset(OPERATIONS), "容器仓库凭据"),
    ("~/.netrc", frozenset(OPERATIONS), ".netrc 明文凭据"),
    ("~/.git-credentials", frozenset(OPERATIONS), "git 明文凭据"),
    ("~/.config/gh", frozenset(OPERATIONS), "GitHub CLI 凭据"),
    ("~/.kube", frozenset(OPERATIONS), "Kubernetes 凭据"),
    ("~/.config/gcloud", frozenset(OPERATIONS), "gcloud 凭据"),
    ("/etc/shadow", frozenset(OPERATIONS), "影子口令"),
    ("/etc/gshadow", frozenset(OPERATIONS), "影子口令"),
    ("/etc/sudoers", frozenset(OPERATIONS), "sudo 策略"),
    ("/root", frozenset(OPERATIONS), "root 家目录"),
    ("~/.bashrc", frozenset(OPERATIONS), "shell 配置"),
    ("~/.bash_profile", frozenset(OPERATIONS), "shell 配置"),
    ("~/.profile", frozenset(OPERATIONS), "shell 配置"),
    ("~/.zshrc", frozenset(OPERATIONS), "shell 配置"),
    ("~/.bash_history", frozenset(OPERATIONS), "shell 历史（可能含凭据）"),
    ("~/.zsh_history", frozenset(OPERATIONS), "shell 历史（可能含凭据）"),
    ("~/.avid", frozenset(OPERATIONS), "agent 配置与审计（模型不可读写）"),
)

# Project tier defaults, relaxable one by one by a SYSTEM allow written by the host user.
PROJECT_PATH_RULES: tuple[tuple[str, frozenset[str], str, str], ...] = (
    (".git/hooks", frozenset({OPERATION_WRITE}), "git hook 会在后续 git 操作里执行任意代码", VERDICT_DENY),
    (".git/config", frozenset({OPERATION_WRITE}), "git 配置可改 URL/凭据助手", VERDICT_DENY),
    (".github/workflows", frozenset({OPERATION_WRITE}), "CI 定义会在带机密的环境里执行", VERDICT_DENY),
    (".avid", frozenset({OPERATION_WRITE}), "agent 的配置与审计不接受自改", VERDICT_DENY),
    (".env", frozenset(OPERATIONS), ".env 通常含凭据", VERDICT_ASK),
    (".env.*", frozenset(OPERATIONS), ".env 通常含凭据", VERDICT_ASK),
)


def builtin_rules(*, root: str | None = None) -> tuple[Rule, ...]:
    """Returns the built-in ADMIN and PROJECT rules, the definition the tests check against."""
    rules: list[Rule] = [
        Rule(
            tier=TIER_ADMIN,
            path=path,
            operations=operations,
            verdict=VERDICT_DENY,
            reason=reason,
            source="builtin",
            relaxable=False,
        )
        for path, operations, reason in ADMIN_PATH_RULES
    ]
    rules.extend(
        Rule(
            tier=TIER_PROJECT,
            path=path,
            operations=operations,
            verdict=verdict,
            reason=reason,
            source="builtin",
            relaxable=True,
        )
        for path, operations, reason, verdict in PROJECT_PATH_RULES
    )
    return tuple(rules)


def _read_policy(path: Path, *, strict: bool) -> dict | None:
    """Parses a TOML policy file; strict raises on damage so the SYSTEM tier fails closed."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        if strict:
            raise PolicyConfigError(f"{path} 读不出来：{exc}") from exc
        logger.warning("项目策略 %s 损坏已忽略：%s", path, exc)
        return None

    problem = _schema_problem(data)
    if problem is not None:
        if strict:
            raise PolicyConfigError(f"{path} 不合 schema：{problem}")
        logger.warning("项目策略 %s 不合 schema 已忽略：%s", path, problem)
        return None
    return data


def _schema_problem(data: object) -> str | None:
    if not isinstance(data, dict):
        return "顶层必须是表"
    for section in data:
        if section not in {"deny", "allow"}:
            return f"未知段 [{section}]"
        table = data[section]
        if not isinstance(table, dict):
            return f"[{section}] 必须是表"
        for operation in table:
            if operation not in OPERATIONS:
                return f"[{section}] 未知口径 {operation}"
            if not isinstance(table[operation], list) or not all(
                isinstance(item, str) for item in table[operation]
            ):
                return f"[{section}].{operation} 必须是字符串列表"
    return None


def _rules_from(parsed: dict, *, tier: str, source: str) -> list[Rule]:
    rules: list[Rule] = []
    for operation, patterns in (parsed.get("deny") or {}).items():
        for pattern in patterns:
            rules.append(
                Rule(
                    tier=tier,
                    path=pattern,
                    operations=frozenset({operation}),
                    verdict=VERDICT_DENY,
                    reason=f"{source} 声明的 deny",
                    source=source,
                )
            )
    return rules


def _allow_from(parsed: dict) -> list[Rule]:
    # Relaxations always belong to the SYSTEM tier, no matter which file they were read from.
    rules: list[Rule] = []
    for operation, patterns in (parsed.get("allow") or {}).items():
        for pattern in patterns:
            rules.append(
                Rule(
                    tier=TIER_SYSTEM,
                    path=pattern,
                    operations=frozenset({operation}),
                    verdict="allow",
                    reason="SYSTEM ALLOW",
                    source="system_config",
                )
            )
    return rules


__all__ = [
    "ADMIN_PATH_RULES",
    "Ladder",
    "OPERATIONS",
    "OPERATION_READ",
    "OPERATION_WRITE",
    "PROJECT_PATH_RULES",
    "PolicyConfigError",
    "Rule",
    "SYSTEM_POLICY_ENV",
    "TIERS",
    "TIER_ADMIN",
    "TIER_PROJECT",
    "TIER_SYSTEM",
    "TIER_USER",
    "VERDICT_ASK",
    "VERDICT_DENY",
    "builtin_rules",
    "expand_pattern",
    "path_hit",
]
