"""subagent：把互不依赖的子任务派给多个子 agent 并行处理。

子 agent **复用 agent_loop**，而不是另写一份循环——权限三闸门、四个 hook 事件、
输出截断、TODO 绑定都已经在 agent_loop 里，重写等于把它们复制到第二处。

层级只有一层：``SUB_TOOLS`` 里没有 ``subagent``，结构上不可能递归派生。
（参考实现里这个工具叫 "task tool"，本项目命名为 ``subagent``。）
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING, Any

from ..config import Config, load_config
from ..llm import chat_completion

if TYPE_CHECKING:  # 运行时导入会成环（state.py 要 import 本模块所在的包）
    from ..state import RunState

logger = logging.getLogger("avid.subagent")

SUB_SYSTEM = (
    "你是 Avid 的 subagent，被派去独立完成一个子任务。"
    "专注把这一件事做完，然后给出一份简短、自包含的结论摘要——"
    "主 agent 只看得到你的摘要，看不到你的中间过程。"
    "不要反问、不要索要更多信息，用你能用的工具自己解决。"
)

SUBAGENT_MAX_TURNS = 30

# 整批共用一个墙钟预算，不是每个子任务各 300 秒——否则 N 个任务最坏要等 N×300 秒。
SUBAGENT_TIMEOUT_SECONDS = 300.0

MAX_PARALLEL = 4


def _no_summary(text: str) -> str:
    """空摘要给个明确占位，别让汇总结果里出现空白块。"""
    return text.strip() or "(no summary)"


def run_subagent(
    prompt: str,
    *,
    config: Config | None = None,
    auto_approve: bool = False,
    chat: Callable[..., Any] = chat_completion,
) -> str:
    """跑一个子 agent，返回它的结论摘要。"""
    # 延迟导入：agent.py 需要 import 本模块来注册工具，顶部导入会成环。
    from ..agent import RoundLimitExceeded, agent_loop
    from . import SUB_HANDLERS, SUB_TOOLS

    messages = [{"role": "user", "content": prompt}]
    try:
        text = agent_loop(
            messages,
            system=SUB_SYSTEM,
            tools=SUB_TOOLS,
            registry=SUB_HANDLERS,
            config=config or load_config(),
            chat=chat,
            auto_approve=auto_approve,
            max_rounds=SUBAGENT_MAX_TURNS,
        )
    except RoundLimitExceeded:
        return (
            f"Subagent stopped after {SUBAGENT_MAX_TURNS} turns without a final answer."
        )

    return _no_summary(text)


def _validate(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        _bad("tasks 必须是非空数组")

    if len(raw) > MAX_PARALLEL:
        _bad(f"一次最多派发 {MAX_PARALLEL} 个 subagent，收到 {len(raw)} 个；请分批调用")

    tasks: list[dict[str, str]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            _bad(f"第 {index} 项不是对象")
        description = item.get("description")
        prompt = item.get("prompt")
        if not isinstance(description, str) or not description.strip():
            _bad(f"第 {index} 项的 description 不能为空")
        if not isinstance(prompt, str) or not prompt.strip():
            _bad(f"第 {index} 项的 prompt 不能为空；每条任务都要自包含")
        tasks.append({"description": description.strip(), "prompt": prompt.strip()})

    return tasks


def _bad(message: str) -> None:
    raise ValueError(message)


def _collect(future: Any, deadline: float, timeout: float) -> str:
    """单个子任务的结果；失败或超时只影响它自己。"""
    try:
        return str(future.result(timeout=max(0.0, deadline - time.monotonic())))
    except FutureTimeoutError:
        return f"Subagent timed out after {timeout:.0f} seconds."
    except Exception as exc:  # 一个子任务失败不该拖垮其它子任务
        return f"Subagent failed: {exc}"


def _render(tasks: list[dict[str, str]], results: list[str]) -> str:
    total = len(tasks)
    header = (
        "已运行 1 个 subagent：" if total == 1 else f"已并行运行 {total} 个 subagent："
    )
    # 再兜一层：run_subagent 已经保证非空，但替换进来的 runner 未必遵守，
    # 输出契约不该因此破掉。
    blocks = [
        f"=== {index}/{total} · {task['description']} ===\n{_no_summary(result)}"
        for index, (task, result) in enumerate(zip(tasks, results), start=1)
    ]
    return header + "\n\n" + "\n\n".join(blocks)


def subagent(
    args: dict[str, Any],
    *,
    state: "RunState",
    runner: Callable[..., str] | None = None,
    timeout: float = SUBAGENT_TIMEOUT_SECONDS,
) -> str:
    """派发 1..MAX_PARALLEL 个子任务并行执行，等全部结束后汇总。"""
    try:
        tasks = _validate(args.get("tasks"))
    except ValueError as exc:
        return f"错误：{exc}"

    run = run_subagent if runner is None else runner
    config = load_config()
    # 免审批开关从 RunState 读，显式传给每个子运行——子 agent 在别的线程里跑，
    # 隐式状态在那里会静默失效。
    auto_approve = state.auto_approve

    executor = ThreadPoolExecutor(max_workers=min(len(tasks), MAX_PARALLEL))
    try:
        futures = [
            executor.submit(
                run, task["prompt"], config=config, auto_approve=auto_approve
            )
            for task in tasks
        ]
        deadline = time.monotonic() + timeout
        results = [_collect(future, deadline, timeout) for future in futures]
    finally:
        # wait=False：超时的子任务没法杀线程，但不能因此阻塞返回。
        executor.shutdown(wait=False, cancel_futures=True)

    logger.info("subagent 派发 %d 个，全部返回", len(tasks))
    return _render(tasks, results)
