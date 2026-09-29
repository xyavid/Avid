"""工具并发契约：哪些调用可以同时跑，哪些必须独占。

一条批（模型一次回复里的 N 个 ``tool_calls``）在执行前被切成若干**执行段**：
段内的并发安全工具一起跑，遇到独占工具就落一道**屏障**——它自己单独跑，前后两段
绝不与它重叠。切分与调度在 ``runtime/execution.py``，这里只回答"谁安全"。

分类的**声明**在每个工具自己的 ``@tool(concurrency=…)`` 里（registry 单点），本模块
把它聚合成两张表。为什么安全性由工具层声明，而不是执行层猜：

* 安全性是**工具自身**的属性（只读文件 vs 改文件 vs 起子进程 vs 改共享状态），
  只有定义它的地方知道；执行层拿到的只是名字与 JSON 参数。
* 挂在装饰器上且为**必填**参数，新增工具时**必须表态**——漏表态在登记时就报 TypeError；
  ``tests/test_tools_contract.py`` 另外断言两张表与注册表构成一个**划分**。
* 不能放 ``policy/``：``runtime/execution.py`` 对策略层零运行时依赖（A13 门禁）。

判定口径：**只读、不写共享可变状态、不产生副作用**才算并发安全。哪怕"只是读文件"
一旦与另一个写文件的调用重叠也可能读到半个文件，所以写类一律独占。

拿不准就归独占：未知工具名（不在任何一张表里）按独占处理，最坏是慢一点，不会写坏。
"""

from __future__ import annotations

from .registry import specs

#: 并发安全：只读外部状态，不改任何东西。同一段里可以任意多个同时跑。
CONCURRENCY_SAFE: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "safe"
)

#: 独占：会写盘、起进程、改共享状态或再开一层并发。批内充当屏障，单独跑。
EXCLUSIVE: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "exclusive"
)


def is_concurrency_safe(name: str) -> bool:
    """这个工具能否与同段内的其它调用同时跑。不认识的名字一律按不安全处理。"""
    return name in CONCURRENCY_SAFE


__all__ = ["CONCURRENCY_SAFE", "EXCLUSIVE", "is_concurrency_safe"]
