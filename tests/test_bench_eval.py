"""AvidBench 全量：12 条 case × 3 变体（跨会话 case 只跑 full 的两种条件）。

默认不跑（`eval` 不在 addopts 里）。真模型调用有成本与抖动，它是提交前手动跑的
一次实验，不是门禁：

    uv run --env-file .env pytest -q -m eval -s

断言只针对**仪器**（不能有 error 态运行）；通过率是测量结果，会打印出来并落盘到
`benchmarks/runs/`，不写进断言——把它写成断言就等于把一次测量变成了门禁。
"""

from __future__ import annotations

import pytest

from avid.ai.config import ConfigError, load_config
from benchmarks.avidbench import load_cases
from benchmarks.avidbench.runner import run_all
from benchmarks.run import default_out_dir

pytestmark = pytest.mark.eval


def test_full_matrix_produces_a_baseline():
    try:
        config = load_config()
    except ConfigError as exc:
        pytest.skip(f"没有模型配置，跳过真模型评测：{exc}")

    cases = load_cases()
    run_set = run_all(
        cases, ("bare", "core", "full"), config=config, out_dir=default_out_dir()
    )
    summary = run_set.summary()
    print("\n" + summary)
    broken = [item for item in run_set.results if item.status == "error"]
    assert not broken, summary + "\n仪器出错：\n" + "\n".join(
        f"{item.case_id}/{item.arm}: {item.error}" for item in broken
    )
    assert run_set.results, "一次运行都没产生"
