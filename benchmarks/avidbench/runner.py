"""把 case、变体、工作区、判定器编排成一次运行。

runner 不判断"agent 应该怎么做"，只做五件事：物化 → 装配 → 跑循环 → 判最终状态
→ 落盘。两条边界值得写下来：

1. **不改 runtime**。变体差异只经既有注入点（`chat` / `tools` / `registry` /
   `state` / `hooks` / `on_message` / `on_event`）表达；
2. **硬超时用既有的取消检查点**。墙钟到了就 `state.cancel("timeout")`，
   循环在步骤边界抛 `RunCancelled`——没有新增机制。

`resolved` 的判定优先级：运行本身没走完（超时 / 轮数上限 / 模型错误）**一律不算成功**，
即使此刻的回答恰好满足判定器——那是运气，不是能力。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from avid.ai.client import LLMError, chat_completion
from avid.ai.config import Config
from avid.runtime.context import ContextBudget
from avid.runtime.loop import RunCancelled
from avid.runtime.state import RunState
from avid.session import MemorySessionRepo, SessionRecorder, messages_for_branch

from . import graders as graders_module
from .case import FIXTURES_ROOT, Case
from .result import RunResult, RunSet, arm_name, current_commit
from .telemetry import Telemetry
from .variants import Variant, hooks_for, run_agent, spec
from .workspace import manifest, materialized


class BenchmarkError(Exception):
    """编排层错误：参数组合不合法、fixture 缺失等。"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_case(
    case: Case,
    variant_name: str,
    *,
    config: Config,
    chat: Callable[..., Any] | None = None,
    out_dir: str | Path | None = None,
    context_chars: int | None = None,
) -> list[RunResult]:
    """跑一个 case 的一个变体。跨会话 case 返回多条结果（phase1 + 两种条件）。

    `context_chars` 是**单变量对照**用的注入值：None = 内核默认阈值。它只对 `avid`
    循环生效，且会写进结果的 `overrides`——否则两组数字放在一起没法解释。
    """
    variant = spec(variant_name)
    if case.followup is not None:
        if variant.name != "full":
            raise BenchmarkError(
                f"跨会话 case {case.id} 只跑 full 变体：bare/core 没有会话层，"
                "「续接 vs 不续接」在它们身上不可表达"
            )
        return _run_followup(
            case, variant, config=config, chat=chat, out_dir=out_dir, context_chars=context_chars
        )
    with materialized(case) as root:
        return [
            _execute(
                case,
                variant,
                config=config,
                chat=chat,
                root=root,
                messages=[{"role": "user", "content": case.prompt}],
                phase=0,
                condition="default",
                out_dir=out_dir,
                recorder=None,
                context_chars=context_chars,
            )
        ]


def _run_followup(
    case: Case,
    variant: Variant,
    *,
    config: Config,
    chat: Callable[..., Any] | None,
    out_dir: str | Path | None,
    context_chars: int | None = None,
) -> list[RunResult]:
    """跨会话 case：第一轮把事实交给 agent，第二轮比较「续接历史」与「干净上下文」。

    `resume` 条件的历史来自真实的会话投影（`messages_for_branch`），不是把消息列表
    直接抄回去——这样量到的就是生产路径上的那套机制。`fresh` 是它的对照：第二轮
    只有新 prompt，第一轮的事实**没有**被偷偷带过来。
    """
    repo = MemorySessionRepo(workspace=case.id)
    session = repo.create(workspace=case.id)
    recorder = SessionRecorder(session)
    recorder.ensure_branch()
    results: list[RunResult] = []
    try:
        with materialized(case) as root:
            results.append(
                _execute(
                    case,
                    variant,
                    config=config,
                    chat=chat,
                    root=root,
                    messages=[{"role": "user", "content": case.prompt}],
                    phase=0,
                    condition="phase1",
                    out_dir=out_dir,
                    recorder=recorder,
                    context_chars=context_chars,
                )
            )
            history = messages_for_branch(session, recorder.branch)
            for condition in ("resume", "fresh"):
                messages = [
                    *(history if condition == "resume" else []),
                    {"role": "user", "content": str(case.followup)},
                ]
                results.append(
                    _execute(
                        case,
                        variant,
                        config=config,
                        chat=chat,
                        root=root,
                        messages=messages,
                        phase=1,
                        condition=condition,
                        out_dir=out_dir,
                        recorder=recorder,
                        context_chars=context_chars,
                    )
                )
    finally:
        if not session.closed:
            session.close()
        repo.close()
    return results


def _execute(
    case: Case,
    variant: Variant,
    *,
    config: Config,
    chat: Callable[..., Any] | None,
    root: Path,
    messages: list[dict[str, Any]],
    phase: int,
    condition: str,
    out_dir: str | Path | None,
    recorder: SessionRecorder | None,
    context_chars: int | None = None,
) -> RunResult:
    telemetry = Telemetry()
    state = RunState.for_run(
        auto_approve=True,  # 离线跑：权限一律预授权，拒绝数由事件流统计
        observer=telemetry.on_event,
        workspace_root=str(root),
        hooks=hooks_for(variant),
    )
    # 注入的阈值只对 avid 循环有作用面：bare 没有压缩，给它一个阈值等于无声无效，
    # 所以既不传也不记（否则结果里会出现一个不存在的差异来源）。
    budget = (
        ContextBudget(context_chars=context_chars)
        if context_chars is not None and variant.loop == "avid"
        else None
    )
    overrides = {"context_chars": context_chars} if budget is not None else {}

    def emit(message: dict[str, Any]) -> None:
        telemetry.on_message(message)
        if recorder is not None:
            recorder.on_message(message)

    watchdog = threading.Timer(case.limits.timeout_seconds, state.cancel, args=("timeout",))
    watchdog.daemon = True
    watchdog.start()
    started = time.monotonic()
    answer = ""
    status = "unresolved"
    error: str | None = None
    try:
        answer = run_agent(
            variant,
            messages=messages,
            config=config,
            chat=chat or chat_completion,
            state=state,
            on_message=emit,
            budget=budget,
        )
    except RunCancelled as exc:
        status = "timeout" if state.cancel_reason == "timeout" else "cancelled"
        error = str(exc)
    except LLMError as exc:
        status, error = "llm_error", str(exc)
    except Exception as exc:  # 编排层也要把任何异常变成可观察的终态
        status, error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        watchdog.cancel()
    wall_time_ms = int((time.monotonic() - started) * 1000)

    # 只读约束是信息，不是判定：agent 自己写了个草稿文件不算失败，但它要出现在
    # 记录里（`workspace_pristine=False`）——否则"只读"只是我们嘴上说的。
    files = manifest(root)
    pristine = files == manifest(FIXTURES_ROOT / case.fixture)

    grader_results: list[dict[str, Any]] = []
    specs = case.graders if phase == case.scored_phase else ()
    if specs:
        results = graders_module.run_graders(specs, workspace=root, answer=answer)
        grader_results = [item.to_dict() for item in results]
        if status == "unresolved":  # 运行没走完就不改判
            status = "resolved" if all(item.passed for item in results) else "unresolved"
    elif status == "unresolved":
        status = "unscored"

    result = RunResult(
        case_id=case.id,
        category=case.category,
        variant=variant.name,
        condition=condition,
        phase=phase,
        resolved=status == "resolved",
        status=status,
        answer=answer,
        error=error,
        model=config.model,
        commit=current_commit(),
        started_at=_now(),
        wall_time_ms=wall_time_ms,
        graders=grader_results,
        variant_spec=variant.spec_dict(),
        overrides=overrides,
        metrics=telemetry.metrics(),
        workspace_files=files,
        workspace_pristine=pristine,
        trajectory=telemetry.trajectory,
    )
    if out_dir is not None:
        directory = Path(out_dir) / case.id / arm_name(variant.name, condition)
        result.save(directory)
    return result


def run_all(
    cases: list[Case],
    variant_names: tuple[str, ...] = ("bare", "core", "full"),
    *,
    config: Config,
    chat_factory: Callable[[Case, str], Callable[..., Any]] | None = None,
    out_dir: str | Path | None = None,
    context_chars: int | None = None,
) -> RunSet:
    """跑完整矩阵并落盘。跨会话 case 只跑 full（在 `run_case` 里判定）。"""
    results: list[RunResult] = []
    for case in cases:
        for name in variant_names:
            if case.followup is not None and name != "full":
                continue
            chat = chat_factory(case, name) if chat_factory is not None else None
            results.extend(
                run_case(
                    case,
                    name,
                    config=config,
                    chat=chat,
                    out_dir=out_dir,
                    context_chars=context_chars,
                )
            )
    run_set = RunSet(
        results=results,
        root=Path(out_dir) if out_dir is not None else None,
        started_at=_now(),
        commit=current_commit(),
        model=config.model,
        cases=[case.id for case in cases],
        variants=list(variant_names),
        overrides=(
            {"context_chars": context_chars} if context_chars is not None else {}
        ),
    )
    run_set.save()
    return run_set
