"""RunResult / RunSet：一次运行的记录，与一组运行的报表。

**不做加权总分。** 一个 `resolved` 布尔量加一组原始指标，比"87.4 分"可解释得多：
分数一旦合出来，就再也拆不回去，而 benchmark 的价值恰恰在于"为什么"。

落盘形态（`runs/` 不入库）：

    runs/<时间>-<commit>/<case>/<variant>/
    ├── result.json       # 状态 + 指标 + grader 逐条 + variant 规格
    ├── trajectory.jsonl  # 逐条消息与步骤事实（研究材料）
    └── answer.txt        # 最终回答原文
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: 状态取值。`resolved` / `unresolved` 由判定器决定，其余是"运行本身没走完"。
STATUSES = (
    "resolved",
    "unresolved",
    "unscored",
    "timeout",
    "cancelled",
    "round_limit",
    "llm_error",
    "error",
)


def current_commit() -> str:
    """当前 commit 短哈希。取不到就返回空串——记录里写「未记录」比编一个好。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def classify(result: "RunResult") -> str:
    """失败分类（`BENCHMARK.md` §7 的五类 + 取消）。成功返回空串。"""
    if result.status == "resolved":
        return ""
    if result.status == "unresolved":
        return "能力"
    if result.status in ("round_limit", "timeout"):
        return "预算"
    if result.status == "llm_error":
        return "模型"
    if result.status == "cancelled":
        return "取消"
    if result.status == "unscored":
        return ""
    return "基础设施"


def arm_name(variant: str, condition: str) -> str:
    """报表与目录共用的分组名：`full--resume` 这种条件也是独立一格。"""
    return variant if condition == "default" else f"{variant}--{condition}"


@dataclass
class RunResult:
    case_id: str
    category: str
    variant: str
    condition: str = "default"
    phase: int = 0
    resolved: bool = False
    status: str = "unresolved"
    answer: str = ""
    error: str | None = None
    model: str = ""
    commit: str = ""
    started_at: str = ""
    wall_time_ms: int = 0
    graders: list[dict[str, Any]] = field(default_factory=list)
    variant_spec: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    workspace_files: dict[str, int] = field(default_factory=dict)
    #: 受判文件是否与 fixture 逐字节一致（簿记目录不计）。只读 case 的期望值是 True；
    #: 它是**信息**而非判定——agent 自己写草稿不算失败，但要看得见。
    workspace_pristine: bool = True
    trajectory: list[dict[str, Any]] = field(default_factory=list)

    @property
    def arm(self) -> str:
        """报表的分组键（与落盘目录同名）。"""
        return arm_name(self.variant, self.condition)

    def to_dict(self, *, with_trajectory: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not with_trajectory:
            data.pop("trajectory", None)
        return data

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with (directory / "trajectory.jsonl").open("w", encoding="utf-8") as handle:
            for entry in self.trajectory:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if self.answer:
            (directory / "answer.txt").write_text(self.answer + "\n", encoding="utf-8")


@dataclass
class RunSet:
    results: list[RunResult] = field(default_factory=list)
    root: Path | None = None
    started_at: str = ""
    commit: str = ""
    model: str = ""
    cases: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)

    # ---------------- 聚合 ----------------

    def arms(self) -> list[str]:
        seen: list[str] = []
        for result in self.results:
            if result.arm not in seen:
                seen.append(result.arm)
        return seen

    def by_case(self) -> dict[str, dict[str, RunResult]]:
        table: dict[str, dict[str, RunResult]] = {}
        for result in self.results:
            table.setdefault(result.case_id, {})[result.arm] = result
        return table

    def summary(self) -> str:
        lines = [
            f"AvidBench v0.1  model={self.model or '未记录'}"
            f"  commit={self.commit or '未记录'}  started={self.started_at or '未记录'}",
            "",
        ]
        header = (
            f"{'arm':<16}{'resolved':>10}{'tokens均值':>12}{'rounds均值':>12}"
            f"{'墙钟均值':>11}{'工具失败':>10}{'拒绝':>6}{'失败分类':>12}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for arm in self.arms():
            group = [item for item in self.results if item.arm == arm]
            scored = [item for item in group if item.status != "unscored"]
            passed = sum(1 for item in scored if item.resolved)
            tokens = _mean([int(item.metrics.get("tokens") or 0) for item in scored])
            rounds = _mean([int(item.metrics.get("rounds") or 0) for item in scored])
            wall = _mean([item.wall_time_ms / 1000 for item in scored])
            failures = sum(int(item.metrics.get("tool_failures") or 0) for item in group)
            denials = sum(int(item.metrics.get("denials") or 0) for item in group)
            reasons = _reason_counts(group)
            lines.append(
                f"{arm:<16}{f'{passed}/{len(scored)}':>10}{tokens:>12}{rounds:>12}"
                f"{wall:>11}{failures:>10}{denials:>6}{reasons:>12}"
            )
        lines.append("")
        lines.append("按 case（ok=判定通过 · x=未通过 · ! =运行没走完 · -=未跑）")
        arms = self.arms()
        lines.append(f"{'case':<24}{'category':<14}" + "".join(f"{arm:>18}" for arm in arms))
        for case_id, row in self.by_case().items():
            category = next(iter(row.values())).category
            cells = ""
            for arm in arms:
                result = row.get(arm)
                cells += f"{_mark(result):>18}"
            lines.append(f"{case_id:<24}{category:<14}{cells}")
        lines.append("")
        lines.append(
            "口径：resolved = 全部确定性 grader 通过；tokens 来自 state.tokens（含每轮 usage）；"
            "rounds = 事件流里最大的轮次。"
        )
        return "\n".join(lines) + "\n"

    def save(self) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        text = self.summary()
        (self.root / "summary.txt").write_text(text, encoding="utf-8")
        (self.root / "results.json").write_text(
            json.dumps(
                {
                    "started_at": self.started_at,
                    "commit": self.commit,
                    "model": self.model,
                    "cases": self.cases,
                    "variants": self.variants,
                    "results": [item.to_dict() for item in self.results],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _mean(values: list[float] | list[int]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def _reason_counts(group: list[RunResult]) -> str:
    counts: dict[str, int] = {}
    for result in group:
        reason = classify(result)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    if not counts:
        return "-"
    return " ".join(f"{name}{count}" for name, count in sorted(counts.items()))


def _mark(result: RunResult | None) -> str:
    if result is None:
        return "-"
    if result.status == "unscored":
        return "~"
    if result.resolved:
        return "ok"
    if result.status == "unresolved":
        return "x"
    return "!"
