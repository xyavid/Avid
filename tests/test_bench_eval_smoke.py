"""AvidBench 冒烟子集：3 条 case × 3 变体。

默认不跑（`eval_smoke` 不在 addopts 里）。先跑它确认仪器通了，再花全量的钱：

    uv run --env-file .env pytest -q -m eval_smoke -s
"""

from __future__ import annotations

import pytest
from support import assert_no_infrastructure_failures, real_config_or_skip

from benchmarks.avidbench import load_cases
from benchmarks.avidbench.runner import run_all
from benchmarks.run import SMOKE_CASES, default_out_dir

pytestmark = pytest.mark.eval_smoke


def test_smoke_subset_runs_end_to_end():
    config = real_config_or_skip()
    cases = load_cases(ids=list(SMOKE_CASES))
    run_set = run_all(
        cases, ("bare", "core", "full"), config=config, out_dir=default_out_dir()
    )
    print("\n" + assert_no_infrastructure_failures(run_set))
