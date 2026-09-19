"""AvidBench 冒烟子集：3 条 case × 3 变体。

默认不跑（`eval_smoke` 不在 addopts 里）。先跑它确认仪器通了，再花全量的钱：

    uv run --env-file .env pytest -q -m eval_smoke -s
"""

from __future__ import annotations

import pytest

from avid.ai.config import ConfigError, load_config
from benchmarks.avidbench import load_cases
from benchmarks.avidbench.runner import run_all
from benchmarks.run import SMOKE_CASES, default_out_dir

pytestmark = pytest.mark.eval_smoke


def test_smoke_subset_runs_end_to_end():
    try:
        config = load_config()
    except ConfigError as exc:
        pytest.skip(f"没有模型配置，跳过真模型评测：{exc}")

    cases = load_cases(ids=list(SMOKE_CASES))
    run_set = run_all(
        cases, ("bare", "core", "full"), config=config, out_dir=default_out_dir()
    )
    summary = run_set.summary()
    print("\n" + summary)
    broken = [item for item in run_set.results if item.status == "error"]
    assert not broken, summary + "\n仪器出错：\n" + "\n".join(
        f"{item.case_id}/{item.arm}: {item.error}" for item in broken
    )
