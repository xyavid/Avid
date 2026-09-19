"""AvidBench 的库层。入口见 `benchmarks/run.py`，用法见 `benchmarks/README.md`。

模块划分（每个模块只做一件事）：

* `case`      —— Case 的 schema、加载与校验；
* `workspace` —— fixture → 临时工作区（跑完即删）；
* `variants`  —— bare / core / full 的规格与装配（三个变体的唯一事实来源）；
* `bare`      —— 基准线的朴素循环（同样走 `execute_batch` 的工具协议）；
* `telemetry` —— `on_event` / `on_message` → 原始指标与轨迹；
* `graders`   —— 五种确定性判定器；
* `result`    —— RunResult / RunSet 与聚合报表；
* `runner`    —— 把上面这些编排成一次运行。
"""

from .case import CATEGORIES, Case, CaseError, Limits, load_case, load_cases
from .graders import GraderError, GraderResult, normalize, run_graders, validate_spec
from .result import RunResult, RunSet
from .runner import run_all, run_case
from .variants import CORE_TOOLS, SYSTEM_PROMPT, VARIANTS, Variant
from .workspace import materialized

__all__ = [
    "CATEGORIES",
    "CORE_TOOLS",
    "SYSTEM_PROMPT",
    "VARIANTS",
    "Case",
    "CaseError",
    "GraderError",
    "GraderResult",
    "Limits",
    "RunResult",
    "RunSet",
    "Variant",
    "load_case",
    "load_cases",
    "materialized",
    "normalize",
    "run_all",
    "run_case",
    "run_graders",
    "validate_spec",
]
