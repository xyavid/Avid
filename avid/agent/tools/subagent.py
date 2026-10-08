"""subagent: hands independent subtasks to several child agents in parallel.

A child agent reuses the main loop, so the permission gates, hook events and output
truncation all come for free.

子运行**也走流式**（阶段 53）：它是「看得见的工人」——前端的子智能体面板要逐字看到它在
写什么、调哪个工具。正文与思考增量经子运行自己的 observer 发出，而 subagent 工具给
observer 打的标记（`{task, index}`）会跟着走，所以父运行的事件流里分得清哪些是它的。
摘要调用仍走非流式（`summarize=chat_completion`），否则压缩摘要的文本会混进它的正文。
"""


from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...providers.byok import resolve_chat
from ...providers.client import chat_completion, stream_completion
from ...providers.config import Config
from ..events import ASSISTANT_DELTA, REASONING_DELTA
from .registry import tool

if TYPE_CHECKING:  # a runtime import would be circular (state.py imports this package)
    from ..events import RunEvent, RunObserver
    from ..hooks import HookRegistry
    from ..state import Checkpointer, RunState

logger = logging.getLogger("avid.subagent")

SUB_SYSTEM = (
    "你是 Avid 的 subagent，被派去独立完成一个子任务。"
    "专注把这一件事做完，然后给出一份简短、自包含的结论摘要——"
    "主 agent 只看得到你的摘要，看不到你的中间过程。"
    "不要反问、不要索要更多信息，用你能用的工具自己解决。"
)

# Children have no round limit, just like the main loop, so their bound is this wall-clock
# budget, which the whole batch shares rather than each task having its own 300 seconds.
SUBAGENT_TIMEOUT_SECONDS = 300.0

MAX_PARALLEL = 4

#: Interval between cancellation checks, so a cancelled parent returns within this long.
CANCEL_POLL_SECONDS = 0.1


def _no_summary(text: str) -> str:
    """Replaces an empty summary with a placeholder so no blank block reaches the result."""
    return text.strip() or "(no summary)"


def streaming_child_chat(state: "RunState") -> Callable[..., Any]:
    """The child's chat: stream the call and emit text/reasoning deltas onto its observer.

    Why the child streams at all: the front end shows a running child's work in the subagent
    panel, and without deltas its words never reach the wire (a child has no message sink and
    persists nothing). The deltas travel the parent's event queue tagged with `{task, index}`,
    which is the same route its tool events already take — one streaming implementation, not two.
    """

    def chat(config: Config, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        return stream_completion(
            config,
            messages,
            on_delta=lambda text: state.emit(ASSISTANT_DELTA, text=text),
            on_reasoning=lambda text: state.emit(REASONING_DELTA, text=text),
            **kwargs,
        )

    return chat


def run_subagent(
    prompt: str,
    *,
    config: Config | None = None,
    auto_approve: bool = False,
    chat: Callable[..., Any] | None = None,
    ask: Any = None,
    permission_mode: str | None = None,
    ledger: Any = None,
    security: Any = None,
    workspace_root: str | None = None,
    scratch: bool = False,
    hooks: "HookRegistry | None" = None,
    observer: "RunObserver | None" = None,
    cancel_probe: "Callable[[], str | None] | None" = None,
    on_state: "Callable[[RunState], None] | None" = None,
    checkpoint: "Checkpointer | None" = None,
) -> str:
    """Runs one child agent and returns its conclusion summary.

    Permission mode, ledger, security, root, the scratch flag and the write-ahead
    checkpointer are forwarded field by field, since a child on another thread inherits no
    run state.
    """
    # A deferred import, since agent/state.py imports this package for the tool tables.
    from ..run import Run
    from ..spec import RunSpec
    from ..state import RunState
    from . import SUB_HANDLERS, SUB_TOOLS

    child_state = RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        observer=observer,
        permission_mode=permission_mode,
        ledger=ledger,
        security=security,
        workspace_root=workspace_root,
        scratch=scratch,
        hooks=hooks,
    )
    # The parent's sink is shared as-is: its tip_seq closure points at the parent's session,
    # which stays open for as long as the parent waits on this batch.
    child_state.checkpoint = checkpoint
    if cancel_probe is not None:
        child_state.cancel_probe = cancel_probe
    if on_state is not None:
        on_state(child_state)

    tools, handlers = (SUB_TOOLS, SUB_HANDLERS)
    if scratch:
        # 临时对话的子运行也摘表：父级的只读约束要跟到底，不能靠"子 agent 大概不会写"
        from . import without_writers

        tools, handlers = without_writers(SUB_TOOLS, SUB_HANDLERS)
    messages = [{"role": "user", "content": prompt}]
    spec = RunSpec.resolve(
        config=config or resolve_chat(),
        # 默认即流式（见 streaming_child_chat）；注入的 chat 走注入的（测试用脚本模型）
        chat=chat if chat is not None else streaming_child_chat(child_state),
        # 摘要必须绕开流式：它的文本不是子运行说的话，混进正文就分不出哪句是结论
        summarize=chat_completion,
        instructions=SUB_SYSTEM,
        tools=tools,
        registry=handlers,
    )
    outcome = Run(messages, spec, state=child_state).run()

    return _no_summary(outcome.text)


def _validate(raw: Any) -> list[dict[str, str]]:
    """Validates the task list, raising ValueError with text the model can act on."""
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
    """Raises a task-list validation problem."""
    raise ValueError(message)


def _collect(
    future: Any, deadline: float, timeout: float, *, state: "RunState"
) -> str:
    """Returns one subtask's result, where a failure or timeout affects only that subtask.

    A cancelled parent converges early rather than waiting for a slow child to finish.
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
        except Exception as exc:  # one failing subtask must not take the others down
            return f"Subagent failed: {exc}"


def _render(tasks: list[dict[str, str]], results: list[str]) -> str:
    """Renders the header and one block per subtask, in the order the tasks were given."""
    total = len(tasks)
    header = (
        "已运行 1 个 subagent：" if total == 1 else f"已并行运行 {total} 个 subagent："
    )
    # A second guard: run_subagent already guarantees a non-empty summary, but a replaced
    # runner may not, and the output contract should not break.
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
    # It owns a thread pool already, so joining a concurrent segment would nest concurrency.
    concurrency="exclusive",
)
def subagent(
    args: dict[str, Any],
    *,
    state: "RunState",
    runner: Callable[..., str] | None = None,
    timeout: float = SUBAGENT_TIMEOUT_SECONDS,
) -> str:
    """Dispatches 1..MAX_PARALLEL subtasks in parallel and summarizes them once all return."""
    try:
        tasks = _validate(args.get("tasks"))
    except ValueError as exc:
        return f"错误：{exc}"

    run = run_subagent if runner is None else runner
    # 子运行跟父运行用同一个模型与推理强度（界面选的那两个）；没有覆盖就一起按设置解析——
    # 否则用户选了 A 模型，子 agent 悄悄按绑定里的 B 跑。
    config = resolve_chat(model=state.model_ref, effort=state.effort)
    # Auto-approval, the ask callback, the permission mode and the ledger are read from the run
    # state and passed explicitly, since implicit state does not follow a child to its thread.
    auto_approve = state.auto_approve
    ask = state.ask
    permission_mode = state.permission_mode
    scratch = state.scratch
    ledger = state.ledger
    # The whole security spec is reused, so child verdicts join the same sandbox and audit.
    security = state.security
    workspace_root = state.workspace_root
    # Write-ahead checkpoints keep landing in the parent session's checkpoint directory.
    checkpoint = state.checkpoint
    # The child takes a copy of the parent registry, so user hooks apply while callbacks the
    # child adds stay out of the parent run.
    hooks = state.hooks.copy()

    deadline = time.monotonic() + timeout

    def probe_for(description: str) -> "Callable[[], str | None]":
        """Returns the child's external cancellation source, which reports but never interrupts."""

        def check() -> str | None:
            if state.cancelled:
                return state.cancel_reason or "cancelled"
            if time.monotonic() >= deadline:
                return f"subagent 批墙钟（{timeout:.0f} 秒）已到"
            return None

        return check

    def observer_for(index: int, description: str) -> "RunObserver | None":
        """Tags child events as belonging to this batch before forwarding them to the parent.

        The tag lets the front end fold child rounds into this batch's tool card.
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
        """Returns a callback recording a child run state under its task index."""

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
                scratch=scratch,
                on_state=remember(index),
                checkpoint=checkpoint,
            )
            for index, task in enumerate(tasks)
        ]
        results = [
            _collect(future, deadline, timeout, state=state) for future in futures
        ]
    finally:
        # No waiting: a timed-out subtask cannot be killed, but it carries a probe, so it stops
        # at its next checkpoint instead of running an uninformed loop to completion.
        executor.shutdown(wait=False, cancel_futures=True)

    # Usage is adopted even for timed-out or cancelled subtasks, since spent tokens are real.
    for child_state in child_states.values():
        state.adopt_child_usage(child_state)

    logger.info("subagent 派发 %d 个，全部返回", len(tasks))
    return _render(tasks, results)
