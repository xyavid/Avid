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
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..ai.client import chat_completion
from ..ai.config import Config, load_config
from ..policy.permission import DEFAULT_MODE
from .registry import tool

if TYPE_CHECKING:  # 运行时导入会成环（state.py 要 import 本模块所在的包）
    from ..runtime.events import RunEvent, RunObserver
    from ..runtime.hooks import HookRegistry
    from ..runtime.state import RunState

logger = logging.getLogger("avid.subagent")

SUB_SYSTEM = (
    "你是 Avid 的 subagent，被派去独立完成一个子任务。"
    "专注把这一件事做完，然后给出一份简短、自包含的结论摘要——"
    "主 agent 只看得到你的摘要，看不到你的中间过程。"
    "不要反问、不要索要更多信息，用你能用的工具自己解决。"
)

# 轮数：子 agent 与主循环一样**没有轮数上限**（终止条件只有"模型不再请求工具"与
# 取消）。它的边界是墙钟预算——下面这个。旧代码写死过 30 轮，等于给子任务塞了一个
# 主任务没有的预算：同一件事在主 agent 里能做完，派给子 agent 反而被判失败。
#
# 整批共用一个墙钟预算，不是每个子任务各 300 秒——否则 N 个任务最坏要等 N×300 秒。
SUBAGENT_TIMEOUT_SECONDS = 300.0

MAX_PARALLEL = 4

#: collect 轮询的间隔：父取消后最多再等这么久就返回（子任务自会在检查点停）。
CANCEL_POLL_SECONDS = 0.1


def _no_summary(text: str) -> str:
    """空摘要给个明确占位，别让汇总结果里出现空白块。"""
    return text.strip() or "(no summary)"


def run_subagent(
    prompt: str,
    *,
    config: Config | None = None,
    auto_approve: bool = False,
    chat: Callable[..., Any] = chat_completion,
    ask: Any = None,
    permission_mode: str = DEFAULT_MODE,
    ledger: Any = None,
    security: Any = None,
    workspace_root: str | None = None,
    hooks: "HookRegistry | None" = None,
    observer: "RunObserver | None" = None,
    cancel_probe: "Callable[[], str | None] | None" = None,
    on_state: "Callable[[RunState], None] | None" = None,
) -> str:
    """跑一个子 agent，返回它的结论摘要。

    ``ask`` 由父运行注入并前传：没有它，子 agent 的审批会落到 stdin 上——在
    uvicorn 进程里那是 EOF 或永久阻塞。这是**既有缺陷的修复**，只是 CLI 下被
    终端与 ``_ASK_LOCK`` 掩盖了（设计文档 §7.2）。

    ``permission_mode`` / ``ledger`` / ``security`` / ``workspace_root`` 同理必须逐字段
    前传：子 agent 在别的线程跑，``RunState`` 不跨线程继承。漏传就出两种事故——漏 mode
    会让**最严一档被静默绕过**（父运行 manual、子 agent 却按默认值放行），漏 ``security``
    会让子 agent 自己重算一份规格（沙箱可能不是同一个、审计会分成两条）。因此有一条
    专门的用例逐个字段盯着。

    阶段 30c 起子运行由本函数**自建并持有引用**（不再让 agent_loop 默认创建）：

    * ``observer`` 前传——子运行的轮次与工具事件进父事件流（调用方负责打标记），
      否则前端只有一张工具卡，里面发生了几轮、调了什么工具全看不见；
    * ``cancel_probe`` 注入外部取消源（父运行取消 / 批墙钟），子循环在检查点感知；
    * ``on_state`` 把子 RunState 交给调用方——返回后调用方据此把子 token 并进
      父台账（``RunState.adopt_child_usage``）。
    """
    # 延迟导入：runtime/state.py 要 import 本包来拿工具表，顶部导入会成环。
    from ..runtime.loop import agent_loop
    from ..runtime.state import RunState
    from . import SUB_HANDLERS, SUB_TOOLS

    child_state = RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        observer=observer,
        permission_mode=permission_mode,
        ledger=ledger,
        security=security,
        workspace_root=workspace_root,
        hooks=hooks,
    )
    if cancel_probe is not None:
        child_state.cancel_probe = cancel_probe
    if on_state is not None:
        on_state(child_state)

    messages = [{"role": "user", "content": prompt}]
    text = agent_loop(
        messages,
        system=SUB_SYSTEM,
        tools=SUB_TOOLS,
        registry=SUB_HANDLERS,
        config=config or load_config(),
        chat=chat,
        state=child_state,
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


def _collect(
    future: Any, deadline: float, timeout: float, *, state: "RunState"
) -> str:
    """单个子任务的结果；失败或超时只影响它自己。

    父运行取消时**提前收敛**：不再等慢子任务自然结束（子循环已通过 probe 在检查点
    停下，这里只是让父循环尽快回到自己的取消检查点）。轮询间隔是
    ``CANCEL_POLL_SECONDS``，正常完成的路径不受影响（``future.result`` 一次等到）。
    """
    remaining = deadline - time.monotonic()
    while True:
        if state.cancelled:
            return "运行已取消，本次子任务未等待完成。"
        try:
            return str(future.result(timeout=min(max(remaining, 0.0), CANCEL_POLL_SECONDS)))
        except FutureTimeoutError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
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
        for index, (task, result) in enumerate(zip(tasks, results, strict=True), start=1)
    ]
    return header + "\n\n" + "\n\n".join(blocks)


@tool(
    name="subagent",
    description="把互不依赖的子任务派给多个 subagent 并行处理，全部结束后汇总各自的结果。"
    "【只在任务可拆分、且子任务之间没有共享状态与先后依赖时使用】："
    "存在强依赖、需要共享同一份上下文、或一步就能做完的，不要用，直接自己做。"
    "subagent 看不到你和用户的对话，只会收到你在 prompt 里写的那段说明——"
    "所以每条任务都要自包含：写清背景、要做什么、期望的输出格式。"
    "一次最多 4 个。",
    properties={
        "tasks": {
            "type": "array",
            "description": "要并行处理的子任务，每项互相独立、没有先后顺序。",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "一句话说明这个子任务干什么，用于在汇总结果里标注归属。",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "发给该 subagent 的完整指令，自包含：背景、要做什么、期望输出。",
                    },
                },
                "required": ["description", "prompt"],
                "additionalProperties": False,
            },
        }
    },
    required=("tasks",),
    # 自己已经有线程池：并进并发段会变成嵌套并发，线程数与预算都失控。
    concurrency="exclusive",
)
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
    # 免审批开关、审批回调、权限模式与账本都从 RunState 读，显式传给每个子运行——
    # 子 agent 在别的线程里跑，隐式状态在那里会静默失效。账本共用一本，于是
    # "同意一次即生效"覆盖整个运行（含子 agent）。
    auto_approve = state.auto_approve
    ask = state.ask
    permission_mode = state.permission_mode
    ledger = state.ledger
    # 规格整份复用：同一个沙箱、同一本阶梯、同一条审计流（子 agent 的裁决也进同一份记录）。
    security = state.security
    workspace_root = state.workspace_root
    # 子运行用父注册表的一份副本：用户注册的 hook 对子 agent 同样生效，而子运行
    # 自己追加的回调不会漏回父运行。
    hooks = state.hooks.copy()

    deadline = time.monotonic() + timeout

    def probe_for(description: str) -> "Callable[[], str | None]":
        """子循环的外部取消源：父运行取消 ∨ 批墙钟到点。只报告，不打断。"""

        def check() -> str | None:
            if state.cancelled:
                return state.cancel_reason or "cancelled"
            if time.monotonic() >= deadline:
                return f"subagent 批墙钟（{timeout:.0f} 秒）已到"
            return None

        return check

    def observer_for(index: int, description: str) -> "RunObserver | None":
        """给子事件打上 subagent 标记后转给父观察者。

        标记让前端把子轮次/子工具折叠进本批的工具卡，而不是当成父运行的散卡；
        svc 的回填也据此跳过（子轮次不该覆盖父运行的 round/tokens 显示）。
        """
        parent_observer = state.observer
        if parent_observer is None:
            return None

        def observe(event: "RunEvent") -> None:
            parent_observer(
                replace(
                    event,
                    data={
                        **event.data,
                        "subagent": {"task": description, "index": index},
                    },
                )
            )

        return observe

    child_states: dict[int, "RunState"] = {}

    def remember(index: int) -> "Callable[[RunState], None]":
        def on_state(child_state: "RunState") -> None:
            child_states.setdefault(index, child_state)

        return on_state

    executor = ThreadPoolExecutor(max_workers=min(len(tasks), MAX_PARALLEL))
    try:
        futures = [
            executor.submit(
                run,
                task["prompt"],
                config=config,
                auto_approve=auto_approve,
                ask=ask,
                permission_mode=permission_mode,
                ledger=ledger,
                security=security,
                workspace_root=workspace_root,
                hooks=hooks,
                observer=observer_for(index, task["description"]),
                cancel_probe=probe_for(task["description"]),
                on_state=remember(index),
            )
            for index, task in enumerate(tasks)
        ]
        results = [
            _collect(future, deadline, timeout, state=state) for future in futures
        ]
    finally:
        # wait=False：超时的子任务没法杀线程，但不能因此阻塞返回。与改动前的差别：
        # 孤儿线程带着 probe——墙钟已到或父已取消，它们会在**下一个检查点**停，
        # 而不是带着不知情的循环跑完整场。
        executor.shutdown(wait=False, cancel_futures=True)

    # 用量并账：子任务哪怕超时/取消，花掉的 token 也是事实。
    for child_state in child_states.values():
        state.adopt_child_usage(child_state)

    logger.info("subagent 派发 %d 个，全部返回", len(tasks))
    return _render(tasks, results)
