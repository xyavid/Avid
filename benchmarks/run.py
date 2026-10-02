"""AvidBench 命令行入口。

    uv run --env-file .env python -m benchmarks.run --list     # 看评测集
    uv run --env-file .env python -m benchmarks.run --smoke    # 3 条 case，先验仪器
    uv run --env-file .env python -m benchmarks.run            # 12 条 × 3 变体

结果落在 `benchmarks/runs/<UTC 时间>-<commit>/`（不入库），同时打印聚合报表。
退出码：0 = 跑完；1 = 有 `error` 态的运行（仪器本身出问题）；2 = 参数或配置错误。
评测数字**不作为**退出码——通过率是测量结果，不是门禁。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from avid.ai.byok import resolve_chat
from avid.ai.config import ConfigError

from .avidbench import VARIANTS, load_suite, suites
from .avidbench.case import CaseError
from .avidbench.result import current_commit
from .avidbench.runner import run_all

#: smoke 子集：一条基础、一条计算、一条恢复。先确认仪器通了，再花全量的钱。
SMOKE_CASES = ("b01_largest_file", "b02_column_sum", "rec02_failing_command")

RUNS_ROOT = Path(__file__).resolve().parent / "runs"

DEFAULT_VARIANTS = ("bare", "core", "full")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.run",
        description="AvidBench：只读任务集 + bare/core/full 三变体（可选注入压缩阈值）",
    )
    parser.add_argument("--cases", help="逗号分隔的 case id；默认该 suite 的全部")
    parser.add_argument(
        "--suite",
        default="all",
        help=f"case 集版本（一个目录一个版本）；默认 all。可用：all、{'、'.join(suites())}",
    )
    parser.add_argument(
        "--variants",
        default=",".join(DEFAULT_VARIANTS),
        help=f"逗号分隔的变体名；默认 {','.join(DEFAULT_VARIANTS)}",
    )
    parser.add_argument("--smoke", action="store_true", help=f"只跑 {len(SMOKE_CASES)} 条 smoke case")
    parser.add_argument(
        "--context-chars",
        type=int,
        default=None,
        help=(
            "注入压缩阈值（字符）——单变量对照用；只对 core/full 生效，会写进结果的 "
            "overrides。不给就是内核默认（40 万）"
        ),
    )
    parser.add_argument("--model", help="覆盖解析出的模型名（会写进结果文件）")
    parser.add_argument("--out", help="结果目录；默认 benchmarks/runs/<时间>-<commit>")
    parser.add_argument("--list", action="store_true", help="只列出评测集")
    args = parser.parse_args(argv)
    if args.context_chars is not None and args.context_chars < 1000:
        parser.error("--context-chars 至少 1000（再低就不是压缩而是删库了）")
    return args


def _case_ids(args: argparse.Namespace) -> list[str] | None:
    if args.smoke:
        return list(SMOKE_CASES)
    if args.cases:
        return [item.strip() for item in args.cases.split(",") if item.strip()]
    return None


def _variant_names(args: argparse.Namespace) -> tuple[str, ...]:
    names = tuple(item.strip() for item in args.variants.split(",") if item.strip())
    unknown = [name for name in names if name not in VARIANTS]
    if unknown:
        raise CaseError(f"未知变体 {unknown}；可用：{'、'.join(VARIANTS)}")
    return names


def default_out_dir() -> Path:
    """一次实验的结果目录：`runs/<UTC 时间>-<commit>`。CLI 与 pytest 薄壳共用。"""
    commit = current_commit() or "nogit"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return RUNS_ROOT / f"{stamp}-{commit}"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = load_suite(args.suite, ids=_case_ids(args))
        variants = _variant_names(args)
    except CaseError as exc:
        print(f"评测集错误：{exc}", file=sys.stderr)
        return 2

    if args.list:
        print(f"{'id':<24}{'category':<14}{'tier':<6}{'fixture':<24}graders  prompt")
        for case in cases:
            prompt = case.prompt.replace("\n", " ")[:36]
            scored = "→ 第二轮" if case.followup else ""
            tier = "-" if case.tier is None else str(case.tier)
            print(
                f"{case.id:<24}{case.category:<14}{tier:<6}{case.fixture:<24}"
                f"{len(case.graders):<8}{prompt}{scored}"
            )
        print(
            f"\n共 {len(cases)} 条 case（suite={args.suite}），变体：{'、'.join(variants)}"
        )
        return 0

    try:
        config = resolve_chat()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.model:
        config = replace(config, model=args.model)

    out_dir = Path(args.out) if args.out else default_out_dir()
    injected = f"，注入 context_chars={args.context_chars}" if args.context_chars else ""
    print(
        f"AvidBench：suite={args.suite}，{len(cases)} 条 case × {len(variants)} 个变体"
        f"{injected} → {out_dir}",
        file=sys.stderr,
    )

    run_set = run_all(
        cases,
        variants,
        config=config,
        out_dir=out_dir,
        context_chars=args.context_chars,
    )
    print(run_set.summary())
    print(f"结果目录：{out_dir}")

    broken = [item for item in run_set.results if item.status in ("error", "llm_error")]
    if broken:
        print(
            f"有 {len(broken)} 次运行是 error / llm_error 态（仪器或配置问题，不是能力问题）",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
