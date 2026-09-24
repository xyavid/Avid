"""四级 deny 阶梯：ADMIN → SYSTEM → PROJECT → USER，下层 allow 永不抵消上层 deny。

原则③的落地。阶梯是**结构**，不是一张更大的正则表：

======================  ==========================================  ================
层                      谁写                                       能不能被放松
======================  ==========================================  ================
ADMIN DENY              代码内置（凭据、块设备、根目录破坏）        不能
SYSTEM DENY             ``~/.avid/policy.toml``（宿主用户自己）      不能
PROJECT DENY            ``<ws>/.avid/policy.toml``（随仓库分发）    不能被本会话的同意放松
USER ALLOW              本次运行的能力账本（``ApprovalLedger``）     只能加，不能减
======================  ==========================================  ================

**唯一**能放松一级 deny 的东西是 ``[allow]`` 段，而它只在 SYSTEM 级（`~/.avid/policy.toml`）
有效：那是"宿主机上的人明确写下来的一句话"，不是仓库自己的声明。PROJECT 文件里的
``[allow]`` 会被**忽略并记一条 note**——原则④：repo 不能削弱宿主机安全策略。

每层各有两条判定口径：

* ``deny`` 命中 → 该动作被拒（分类器与人都不能放行）；
* ``ask`` 命中 → 该动作必须经 REVIEW（manual 问人 / auto 由分类器判，判不准即拒）。

``.env`` 一类"合法但敏感"的目标走 ``ask`` 而不是 ``deny``：deny 是"永远不行"，
把它用在日常要读的文件上只会训练用户习惯性点同意。

**失败模型**（安全配置损坏时不能静默变宽）：

* SYSTEM 文件读不了或不合 schema → 抛 :class:`PolicyConfigError`，运行不启动；
* PROJECT 文件同样坏掉 → 忽略并留 note（它只能加严，忽略不会变宽）。

匹配口径（写清楚比"像 gitignore"更有用）：路径模式先按 ``~``/``$HOME`` 与环境变量展开，
相对模式相对**工作区根**；带 ``*`` 时用 :func:`fnmatch.fnmatchcase`，其中 ``*`` **跨越** ``/``
（与 gitignore 的 ``**`` 语义不同，更严、更不容易漏）；不带 ``*`` 时按"目录前缀"匹配。
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("avid.policy.rules")

TIER_ADMIN = "admin"
TIER_SYSTEM = "system"
TIER_PROJECT = "project"
TIER_USER = "user"
#: 从高到低。优先级由这个顺序定义，新增层级必须插在正确的位置。
TIERS: tuple[str, ...] = (TIER_ADMIN, TIER_SYSTEM, TIER_PROJECT, TIER_USER)
TIER_ORDER: dict[str, int] = {tier: index for index, tier in enumerate(TIERS)}

VERDICT_DENY = "deny"
VERDICT_ASK = "ask"

OPERATION_READ = "read"
OPERATION_WRITE = "write"
OPERATIONS: tuple[str, ...] = (OPERATION_READ, OPERATION_WRITE)

SYSTEM_POLICY_RELPATH = Path(".avid") / "policy.toml"
PROJECT_POLICY_RELPATH = Path(".avid") / "policy.toml"
#: 测试与多宿主场景用的显式覆盖。
SYSTEM_POLICY_ENV = "AVID_POLICY_FILE"


class PolicyConfigError(ValueError):
    """宿主级安全策略读不出来。失败方向是"不启动"，不是"忽略"。"""


def expand_pattern(
    pattern: str, *, root: str | Path | None = None, home: str | Path | None = None
) -> str:
    """把路径模式展开成绝对模式：``~``/``$HOME``/环境变量，相对模式相对工作区根。

    ``~`` 用**注入的** home 展开（默认进程的 ``Path.home()``）：测试与多宿主场景要能
    把"宿主"整个端到端换掉，否则"策略指向哪台机器"就取决于跑进程的用户。
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
    """把路径模式编译成正则。

    自己翻译而不是用 fnmatch，是因为 ``**/`` 必须表示"零层或多层目录"：fnmatch 里
    ``*`` 已经跨 ``/``，``**/private/**`` 会要求"至少一层"，于是用户照着 gitignore
    习惯写的 deny 会**静默失效**——失败方向错了。这里的口径：

    ``**/`` → 零层或多层目录　``**`` → 任意　``*`` → 任意（跨 ``/``）　``?`` → 一个字符

    没有通配符时按目录语义（命中目录本身或它下面的任何东西）。
    """
    out: list[str] = []
    index = 0
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
    return re.compile("^" + body + r"(?:/.*)?$")


def path_hit(text: str, pattern: str) -> bool:
    """``text`` 是否被 ``pattern`` 命中（口径见 :func:`_compile_pattern`）。"""
    return _compile_pattern(pattern).match(text) is not None


@dataclass(frozen=True)
class Rule:
    """一条路径规则。``operations`` 是它管的口径（读 / 写 / 两者）。"""

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
    """装配好的阶梯。``rules`` 已按层去重排序，``check`` 返回**最高层**的命中。"""

    rules: tuple[Rule, ...] = ()
    relaxations: tuple[Rule, ...] = ()
    notes: tuple[str, ...] = ()
    root: str | None = None
    home: str | None = None

    # ---------------------------------------------------------------- 查询

    def check(
        self, target: str | Path, operations: tuple[str, ...] | frozenset[str]
    ) -> Rule | None:
        """最高优先级的命中：``deny`` 全局高于 ``ask``，同为 deny/ask 再比层。

        "deny 高于 ask"是原则③的另一半：PROJECT 的一条 deny 不会被 SYSTEM 的
        ask 挤掉，而 ADMIN 的 ask（本阶段没有）也不会盖过 PROJECT 的 deny。
        """
        wanted = set(operations) or set(OPERATIONS)
        hits = [
            rule
            for rule in self.rules
            if rule.operations & wanted
            and rule.matches(target, root=self.root, home=self.home)
        ]
        if not hits:
            return None
        hits.sort(key=lambda rule: (rule.verdict != VERDICT_DENY, TIER_ORDER[rule.tier]))
        return hits[0]

    def relaxed(self, rule: Rule) -> bool:
        """这条规则是否被 SYSTEM ALLOW 明确放开。不可放松的层永远返回 False。"""
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
        """把"命中但被 SYSTEM ALLOW 放开"的规则滤掉之后的结果。"""
        rule = self.check(target, operations)
        if rule is not None and self.relaxed(rule):
            logger.info("规则 %s 被 SYSTEM ALLOW 放开：%s", rule.path, rule.reason)
            return None
        return rule

    # ---------------------------------------------------------------- 装配

    @classmethod
    def load(
        cls,
        *,
        root: str | Path | None = None,
        home: str | Path | None = None,
        system_path: str | Path | None = None,
        project_path: str | Path | None = None,
    ) -> "Ladder":
        """装配四级阶梯。SYSTEM 损坏 → 抛错；PROJECT 损坏 → 忽略 + note。"""
        home_dir = Path(home) if home is not None else Path.home()
        root_text = str(Path(root).resolve()) if root is not None else None
        notes: list[str] = []

        rules: list[Rule] = list(builtin_rules(root=root_text))
        relaxations: list[Rule] = []

        system_file = Path(system_path) if system_path else _system_policy_path(home_dir)
        if system_file.is_file():
            parsed = _read_policy(system_file, strict=True)
            assert parsed is not None  # strict=True 时要么拿到表、要么抛 PolicyConfigError
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
        """给诊断与测试看的扁平视图。"""
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
    """宿主级策略文件：``AVID_POLICY_FILE`` > ``AVID_HOME/policy.toml`` > ``<家>/.avid/policy.toml``。"""
    override = os.environ.get(SYSTEM_POLICY_ENV)
    if override:
        return Path(override).expanduser()
    if os.environ.get("AVID_HOME"):
        from .userdirs import avid_home

        return avid_home() / "policy.toml"
    return home / SYSTEM_POLICY_RELPATH


#: ADMIN 档：凭据与宿主安全策略本身。读或写都拒，且**不可放松**。
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

#: PROJECT 档内置默认：**可被 SYSTEM ALLOW 逐条放开**（`~/.avid/policy.toml` 的
#: `[allow]` 是宿主机上的人写下的一句话，仓库文件写不了这句话）。
PROJECT_PATH_RULES: tuple[tuple[str, frozenset[str], str, str], ...] = (
    (".git/hooks", frozenset({OPERATION_WRITE}), "git hook 会在后续 git 操作里执行任意代码", VERDICT_DENY),
    (".git/config", frozenset({OPERATION_WRITE}), "git 配置可改 URL/凭据助手", VERDICT_DENY),
    (".github/workflows", frozenset({OPERATION_WRITE}), "CI 定义会在带机密的环境里执行", VERDICT_DENY),
    (".avid", frozenset({OPERATION_WRITE}), "agent 的配置与审计不接受自改", VERDICT_DENY),
    (".env", frozenset(OPERATIONS), ".env 通常含凭据", VERDICT_ASK),
    (".env.*", frozenset(OPERATIONS), ".env 通常含凭据", VERDICT_ASK),
)


def builtin_rules(*, root: str | None = None) -> tuple[Rule, ...]:
    """ADMIN + PROJECT 两档的内置规则（唯一一份定义，测试逐条对表）。"""
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
    """解析一份 TOML 策略文件。``strict`` 时损坏即抛错（SYSTEM 档）。"""
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
