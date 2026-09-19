"""Case：一次评测的输入。

一个 case 由四段构成，**没有第五段**：

* `prompt`  —— 给模型的话（可选的 `followup` 是跨会话的第二轮）；
* `fixture` —— 只读初始状态（一个目录，跑的时候复制进临时工作区）；
* `limits`  —— 轮数与墙钟上限；
* `graders` —— 确定性判定器。

它不知道变体是什么、也不知道怎么跑——把两者组合起来是 `runner` 的事。
判定器判的是**最终状态与最终回答**，不判过程；过程进 `trajectory.jsonl` 供研究。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .graders import validate_spec

BENCH_ROOT = Path(__file__).resolve().parent.parent
CASES_ROOT = BENCH_ROOT / "cases"
FIXTURES_ROOT = BENCH_ROOT / "fixtures"

#: 允许的类别。加类别要同时改这里与 README 的表格——分类是报表的分组维度。
CATEGORIES = ("basic", "long_horizon", "recovery", "subagent", "task", "session")

DEFAULT_MAX_ROUNDS = 12
DEFAULT_TIMEOUT_SECONDS = 300.0


class CaseError(Exception):
    """case 文件不合法。错误信息自带修复方法。"""


@dataclass(frozen=True)
class Limits:
    """一次运行的资源上限。``timeout_seconds`` 是 runner 层的墙钟硬超时。"""

    max_rounds: int = DEFAULT_MAX_ROUNDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    prompt: str
    fixture: str
    limits: Limits = field(default_factory=Limits)
    graders: tuple[dict[str, Any], ...] = ()
    #: 跨会话 case 的第二轮。有它时 grader 只判第二轮（见 `scored_phase`）。
    followup: str | None = None
    notes: str = ""

    @property
    def scored_phase(self) -> int:
        """grader 判第几轮：有 followup 就是第二轮。"""
        return 1 if self.followup else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "fixture": self.fixture,
            "max_rounds": self.limits.max_rounds,
            "timeout_seconds": self.limits.timeout_seconds,
            "graders": [dict(spec) for spec in self.graders],
            "followup": self.followup,
        }


def _require(raw: dict[str, Any], key: str, path: Path) -> Any:
    if key not in raw:
        raise CaseError(f"{path}：缺少必需字段 {key!r}")
    return raw[key]


def _limits(raw: Any, path: Path) -> Limits:
    if raw is None:
        return Limits()
    if not isinstance(raw, dict):
        raise CaseError(f"{path}：[limits] 必须是表")
    unknown = set(raw) - {"max_rounds", "timeout_seconds"}
    if unknown:
        raise CaseError(f"{path}：[limits] 有未知键 {sorted(unknown)}")
    limits = Limits(
        max_rounds=int(raw.get("max_rounds", DEFAULT_MAX_ROUNDS)),
        timeout_seconds=float(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
    )
    if limits.max_rounds < 1:
        raise CaseError(f"{path}：max_rounds 必须 ≥ 1")
    if limits.timeout_seconds <= 0:
        raise CaseError(f"{path}：timeout_seconds 必须 > 0")
    return limits


def load_case(path: str | Path) -> Case:
    """读一个 TOML case 并校验。任何一处不合法都抛 `CaseError`。"""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CaseError(f"{path}：不是合法 TOML（{exc}）") from exc

    case_id = str(_require(raw, "id", path))
    if case_id != path.stem:
        raise CaseError(f"{path}：id={case_id!r} 与文件名 {path.stem!r} 不一致")

    category = str(_require(raw, "category", path))
    if category not in CATEGORIES:
        raise CaseError(
            f"{path}：未知 category {category!r}；可用：{'、'.join(CATEGORIES)}"
        )

    prompt = str(_require(raw, "prompt", path)).strip()
    if not prompt:
        raise CaseError(f"{path}：prompt 不能为空")

    fixture = str(_require(raw, "fixture", path))
    if not (FIXTURES_ROOT / fixture).is_dir():
        raise CaseError(f"{path}：fixture 目录不存在：{FIXTURES_ROOT / fixture}")

    specs = _require(raw, "graders", path)
    if not isinstance(specs, list) or not specs:
        raise CaseError(f"{path}：至少要有一个 grader（没有判定器的 case 不许进评测集）")
    for spec in specs:
        try:
            validate_spec(spec)
        except Exception as exc:  # GraderError：统一转成 CaseError 并带上文件
            raise CaseError(f"{path}：grader 不合法——{exc}") from exc

    followup = raw.get("followup")
    if followup is not None:
        followup = str(followup).strip()
        if not followup:
            raise CaseError(f"{path}：followup 为空就删掉它")

    return Case(
        id=case_id,
        category=category,
        prompt=prompt,
        fixture=fixture,
        limits=_limits(raw.get("limits"), path),
        graders=tuple(dict(spec) for spec in specs),
        followup=followup,
        notes=str(raw.get("notes", "")),
    )


def case_paths(root: str | Path = CASES_ROOT) -> list[Path]:
    return sorted(Path(root).glob("**/*.toml"))


def load_cases(
    root: str | Path = CASES_ROOT, ids: list[str] | None = None
) -> list[Case]:
    """读整个 case 目录（按 id 排序）。`ids` 给定时只留这些 id，缺一个就报错。"""
    cases = [load_case(path) for path in case_paths(root)]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseError(f"case id 重复：{case.id}")
        seen.add(case.id)
    if ids is None:
        return cases
    wanted = list(dict.fromkeys(ids))
    found = {case.id: case for case in cases}
    missing = [item for item in wanted if item not in found]
    if missing:
        raise CaseError(f"case 不存在：{'、'.join(missing)}")
    return [found[item] for item in wanted]
