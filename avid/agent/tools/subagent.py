"""Hands independent subtasks to several child agents in parallel.

Children reuse the main loop — permission gates, hook events and output truncation included —
and stream their words as ``{task, index}``-tagged deltas on the parent's event queue, while
their input is fixed by ``TASK_FIELDS``: the parent only fills values and ``task_brief``
renders the same six-section shape for every child.
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
    "用户消息就是主 agent 给你的任务提示，按 Objective / Scope / Context / Constraints / "
    "Deliverable 五段写："
    "守住 Scope 与 Constraints 的边界，照着 Deliverable 交回一份简短、自包含的结论摘要——"
    "主 agent 只看得到你的摘要，看不到你的中间过程。"
    "不要反问、不要索要更多信息，用你能用的工具自己解决。"
)

#: The six task fields: name plus the hint the parent fills in. Order drives rendering and the
#: error order; the schema's properties/required and ``_validate`` errors all derive from here.
TASK_FIELDS: tuple[tuple[str, str], ...] = (
    ("description", "一句话标题，用于汇总结果与界面上的任务归属"),
    ("objective", "这次要达成什么"),
    ("scope", "在哪些文件、目录或链路里做；范围之外的事不要做"),
    (
        "context",
        "从你与用户的对话里带过来的已知事实：报错、现场、已排除的可能——"
        "subagent 看不到你和用户的对话",
    ),
    ("constraints", "边界：不许做什么（例如不要改文件、不要装依赖、不要提交）"),
    ("deliverable", "要交回什么，逐项写清回报格式"),
)

#: Closing line every task brief ends with; the parent need not write it.
TASK_STOP_LINE = "Stop after completing the deliverable and return the findings to the parent agent."


def task_brief(task: dict[str, str]) -> str:
    """Renders one validated task into the child's first user message, a fixed shape the parent
    cannot vary — a missing section would only make the child guess."""
    sections = [f"{key.capitalize()}:\n{task[key]}" for key, _ in TASK_FIELDS[1:]]
    return "\n\n".join([task["description"], *sections, TASK_STOP_LINE])


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
    """The child's chat: streams the call and emits text/reasoning deltas on its observer, the
    only way its words reach the subagent panel since it has no message sink."""

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
    question: Any = None,
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
    """Runs one child agent and returns its conclusion summary, forwarding permission mode,
    ledger, security, root, scratch and checkpoint field by field since a child thread
    inherits no run state.
    """
    # A deferred import, since agent/state.py imports this package for the tool tables.
    from ..run import Run
    from ..spec import RunSpec
    from ..state import RunState
    from . import SUB_HANDLERS, SUB_TOOLS

    child_state = RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        question=question,
        observer=observer,
        permission_mode=permission_mode,
        ledger=ledger,
        security=security,
        workspace_root=workspace_root,
        scratch=scratch,
        hooks=hooks,
    )
    # The parent's sink is shared as-is: its closure points at the parent session, kept open.
    child_state.checkpoint = checkpoint
    if cancel_probe is not None:
        child_state.cancel_probe = cancel_probe
    if on_state is not None:
        on_state(child_state)

    tools, handlers = (SUB_TOOLS, SUB_HANDLERS)
    if scratch:
        # A scratch child drops writers too: the parent's read-only constraint must reach the end.
        from . import without_writers

        tools, handlers = without_writers(SUB_TOOLS, SUB_HANDLERS)
    messages = [{"role": "user", "content": prompt}]
    spec = RunSpec.resolve(
        config=config or resolve_chat(),
        # Streaming by default; an injected chat wins (tests use a scripted model).
        chat=chat if chat is not None else streaming_child_chat(child_state),
        # Summaries bypass streaming: their text is not the child speaking.
        summarize=chat_completion,
        instructions=SUB_SYSTEM,
        tools=tools,
        registry=handlers,
    )
    outcome = Run(messages, spec, state=child_state).run()

    return _no_summary(outcome.text)


def _validate(raw: Any) -> list[dict[str, str]]:
    """Validates the task list, raising ValueError with text the model can act on; a missing
    field normally never gets here because the protocol layer rejects it first."""
    if not isinstance(raw, list) or not raw:
        _bad("tasks 必须是非空数组")

    if len(raw) > MAX_PARALLEL:
        _bad(f"一次最多派发 {MAX_PARALLEL} 个 subagent，收到 {len(raw)} 个；请分批调用")

    tasks: list[dict[str, str]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            _bad(f"第 {index} 项不是对象")
        task: dict[str, str] = {}
        for key, hint in TASK_FIELDS:
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                _bad(f"第 {index} 项的 {key} 不能为空：{hint}")
            task[key] = value.strip()
        tasks.append(task)

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
    # A second guard: a replaced runner may return no summary, and the output contract holds.
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
    "subagent 看不到你和用户的对话，只会收到你按下面六段写出的任务提示——"
    "每条任务都要自包含、直接能开工：背景写进 context，边界写进 constraints，"
    "回报格式写进 deliverable。"
    "一次最多 4 个。",
    properties={
        "tasks": {
            "type": "array",
            "description": "要并行处理的子任务，每项互相独立、没有先后顺序。",
            "items": {
                "type": "object",
                "properties": {
                    key: {"type": "string", "description": hint}
                    for key, hint in TASK_FIELDS
                },
                "required": [key for key, _ in TASK_FIELDS],
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
    # Children use the parent's model and effort; with no override both resolve from settings.
    config = resolve_chat(model=state.model_ref, effort=state.effort)
    # Auto-approval, the ask callback, the permission mode and the ledger are read from the run
    # state and passed explicitly, since implicit state does not follow a child to its thread.
    auto_approve = state.auto_approve
    ask = state.ask
    # The question channel is inherited: a child's question reaches the same person.
    question = getattr(state, "question", None)
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
                task_brief(task),
                config=config,
                auto_approve=auto_approve,
                ask=ask,
                question=question,
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
        # No waiting: a timed-out subtask cannot be killed, but its probe stops it at a checkpoint.
        executor.shutdown(wait=False, cancel_futures=True)

    # Usage is adopted even for timed-out or cancelled subtasks, since spent tokens are real.
    for child_state in child_states.values():
        state.adopt_child_usage(child_state)

    logger.info("subagent 派发 %d 个，全部返回", len(tasks))
    return _render(tasks, results)
