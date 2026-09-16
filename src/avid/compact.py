"""上下文压缩管线：五步，代价由低到高。

每轮都跑（零 API 调用）：

* ① ``tool_result_budget`` —— 工具结果字符总量超预算就把当前最大的落盘
* ② ``snip_compact`` —— 消息条数超上限就裁掉中间，保留头尾

只在上下文超限时：

* ③ ``micro_compact`` —— 把较早的工具结果落盘，保留最近若干条，仍然不调模型
* ④ ``compact_history`` —— ③ 之后仍超限，才花一次模型调用换摘要，替换历史

兜底：

* ⑤ ``reactive_compact`` —— 模型报 ``prompt_too_long`` 时总结更早历史、保留最近若干条，重试一次

三条硬保证：①②③ 不调用模型；④ 每次运行最多一次；⑤ 最多一次。

所有裁剪只在**安全边界**切开——前一条没有待回应的 ``tool_calls``，且切口处不是
``tool`` 结果。否则工具调用与结果会被拆散，下一次请求会被端点直接拒绝。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .llm import chat_completion

logger = logging.getLogger("avid.compact")

# 阈值集中在这里，按实测调整只改这些数。
TOOL_RESULT_CHAR_BUDGET = 200_000
TOOL_RESULT_KEEP_RECENT = 3
MAX_MESSAGES = 50
SNIP_KEEP_HEAD = 8
SNIP_KEEP_TAIL = 24
CONTEXT_CHAR_LIMIT = 400_000
MICRO_COMPACT_KEEP_RECENT = 3
MICRO_COMPACT_TARGET_RATIO = 0.8
REACTIVE_KEEP_RECENT = 5
SUMMARY_MAX_TOKENS = 4000

# 落盘目录必须在工作区内：read_file 有工作区边界校验，落到外面就再也读不回来了。
SPILL_DIR = ".avid/context"
SPILL_PREFIX = "[已落盘]"

SUMMARY_SYSTEM = (
    "你是上下文压缩器。把给定的对话记录压缩成一份要点摘要，供另一个 agent 接着干活。"
    "必须保留：任务目标、已确认的事实与结论、改动过的文件与关键位置、"
    "试过并已排除的做法、以及当前进行到哪一步、下一步该做什么。"
    "可以丢掉：完整文件内容、完整命令输出、重复的中间推理。"
    "只输出摘要正文。"
)

_spill_seq = 0


@dataclass(frozen=True)
class CompactReport:
    """一次压缩做了什么。循环据此打日志，统计压缩次数。"""

    step: str
    detail: str
    before: int
    after: int

    def describe(self) -> str:
        return f"{self.step} — {self.detail}"


# ---------------- 基础工具 ----------------


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    """粗估上下文字符数：正文 + tool_calls 序列化 + 每条的结构开销。"""
    total = 0
    for message in messages:
        total += len(_text_of(message.get("content"))) + 16
        calls = message.get("tool_calls")
        if calls:
            total += len(json.dumps(calls, ensure_ascii=False, default=str))
    return total


def validate_structure(messages: list[dict[str, Any]]) -> list[str]:
    """返回结构违规列表；空列表表示合法。压缩后必须为空。"""
    problems: list[str] = []
    expected: set[str] = set()

    for index, message in enumerate(messages):
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id"))
            if call_id not in expected:
                problems.append(f"#{index} 的 tool 结果没有对应的 tool_call：{call_id}")
            else:
                expected.discard(call_id)
            continue

        if expected:
            problems.append(f"#{index} 之前有 {len(expected)} 个 tool_call 没收到结果")
            expected = set()

        for call in message.get("tool_calls") or []:
            expected.add(str(call.get("id")))

    if expected:
        problems.append(f"结尾有 {len(expected)} 个 tool_call 没收到结果")
    return problems


def _safe_boundary(messages: list[dict[str, Any]], index: int) -> bool:
    """在 index 处切开是否安全：前一条没有待回应的 tool_calls，且 index 处不是 tool。"""
    if index <= 0 or index >= len(messages):
        return True
    if messages[index].get("role") == "tool":
        return False
    return not messages[index - 1].get("tool_calls")


def _tool_chars(messages: list[dict[str, Any]]) -> int:
    return sum(
        len(_text_of(m.get("content"))) for m in messages if m.get("role") == "tool"
    )


def _is_spilled(content: Any) -> bool:
    return isinstance(content, str) and content.startswith(SPILL_PREFIX)


def _spill_root() -> Path:
    # 延迟导入：agent.py 要 import 本模块，顶部导入会成环。
    from .tools import workspace

    return Path(workspace.WORKSPACE_ROOT) / SPILL_DIR


def _spill(text: str, kind: str) -> str | None:
    """写盘并返回工作区相对路径。压缩不是关键路径，落盘失败就跳过、不抛。"""
    global _spill_seq

    root = _spill_root()
    _spill_seq += 1
    path = root / f"{kind}-{_spill_seq:04d}.txt"
    try:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        logger.warning("compact: 落盘失败，本次跳过")
        return None
    return f"{SPILL_DIR}/{path.name}"


def _notice(path: str, size: int, kind: str) -> str:
    return f"{SPILL_PREFIX} 原{kind}共 {size} 字符，已存至 {path}；需要时用 read_file 读回。"


def _save_transcript(messages: list[dict[str, Any]]) -> str:
    global _spill_seq

    root = _spill_root()
    _spill_seq += 1
    path = root / f"transcript-{_spill_seq:04d}.json"
    try:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("compact: transcript 落盘失败")
        return "（落盘失败）"
    return f"{SPILL_DIR}/{path.name}"


def _summarize(messages: list[dict[str, Any]], *, config: Config, chat: Any) -> str | None:
    request = list(messages) + [{"role": "user", "content": "请把以上对话压缩成要点摘要。"}]
    try:
        turn = chat(
            config,
            request,
            system=SUMMARY_SYSTEM,
            max_tokens=SUMMARY_MAX_TOKENS,
        )
    except Exception as exc:  # 摘要失败不该让整个运行崩掉
        logger.warning("compact: 摘要调用失败：%s", exc)
        return None

    text = str(turn.text).strip()
    return text or None


def _summary_message(summary: str, transcript: str) -> str:
    return (
        "[历史摘要] 之前的对话已被压缩，以下是摘要。\n\n"
        f"{summary}\n\n"
        f"（完整记录：{transcript}；需要细节时用 read_file 读回。）"
    )


# ---------------- ① tool_result_budget ----------------


def tool_result_budget(
    messages: list[dict[str, Any]],
    *,
    budget: int = TOOL_RESULT_CHAR_BUDGET,
    keep_recent: int = TOOL_RESULT_KEEP_RECENT,
) -> CompactReport | None:
    """工具结果字符总量超预算：把最大的一项落盘。

    两条经验规则，都是实测出来的：

    * **最近 ``keep_recent`` 条永不落盘**——最大的那条往往正是模型刚读到、下一步
      要用的，落掉它只会让模型重读，然后下一轮又被落掉。
    * **每次调用最多落一项**——一轮剥掉一批会让模型丢掉刚建立的工作集，
      实测会锁死成"读了被落、落了再读"的循环。这里只做温和的滴水，
      真正的硬压缩交给 ③④。

    另外注意：``budget`` 必须显著大于"``keep_recent`` 条结果的合计大小"，
    否则永远够不到预算线，会变成每轮持续剥工作集。默认 200_000 相对
    单条结果上限（工具的 20_000）× 3 有 3 倍以上余量。
    """
    before = _tool_chars(messages)
    if before <= budget:
        return None

    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indexes) <= keep_recent:
        return None
    spillable = tool_indexes[:-keep_recent]

    candidates = [
        (len(_text_of(messages[index].get("content"))), index)
        for index in spillable
        if not _is_spilled(messages[index].get("content"))
    ]
    if not candidates:
        return None

    size, index = max(candidates)
    if size == 0:
        return None

    path = _spill(_text_of(messages[index].get("content")), "tool-result")
    if path is None:
        return None

    messages[index]["content"] = _notice(path, size, "工具结果")
    return CompactReport(
        "tool_result_budget",
        f"落盘最大的一项工具结果（保留最近 {keep_recent} 条）",
        before,
        _tool_chars(messages),
    )


# ---------------- ② snip_compact ----------------


def snip_compact(
    messages: list[dict[str, Any]],
    *,
    max_messages: int = MAX_MESSAGES,
    keep_head: int = SNIP_KEEP_HEAD,
    keep_tail: int = SNIP_KEEP_TAIL,
) -> CompactReport | None:
    """消息条数超上限：裁掉中间，保留头尾。切口只在安全边界。"""
    before = len(messages)
    if before <= max_messages:
        return None

    head_end = min(keep_head, before)
    tail_start = max(head_end, before - keep_tail)

    while head_end < tail_start and not _safe_boundary(messages, head_end):
        head_end += 1
    while tail_start > head_end and not _safe_boundary(messages, tail_start):
        tail_start -= 1

    if head_end >= tail_start:
        logger.info("compact: snip_compact 找不到安全切口，跳过本轮")
        return None

    dropped = tail_start - head_end
    marker = {
        "role": "user",
        "content": (
            f"[已裁剪] 为控制上下文长度，中间 {dropped} 条消息被移除"
            "（较早的工具结果如需恢复，见其中的落盘路径）。"
        ),
    }
    messages[:] = messages[:head_end] + [marker] + messages[tail_start:]
    return CompactReport("snip_compact", f"裁掉中间 {dropped} 条", before, len(messages))


# ---------------- ③ micro_compact ----------------


def micro_compact(
    messages: list[dict[str, Any]],
    *,
    limit: int = CONTEXT_CHAR_LIMIT,
    keep_recent: int = MICRO_COMPACT_KEEP_RECENT,
    target_ratio: float = MICRO_COMPACT_TARGET_RATIO,
) -> CompactReport | None:
    """上下文超限：把较早的工具结果落盘，保留最近若干条。不调用模型。"""
    before = estimate_chars(messages)
    if before <= limit:
        return None

    target = int(limit * target_ratio)
    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    candidates = tool_indexes[:-keep_recent] if keep_recent else tool_indexes

    spilled = 0
    for index in candidates:
        if estimate_chars(messages) <= target:
            break

        content = messages[index].get("content")
        if _is_spilled(content):
            continue

        size = len(_text_of(content))
        path = _spill(_text_of(content), "tool-result")
        if path is None:
            break

        messages[index]["content"] = _notice(path, size, "工具结果")
        spilled += 1

    if not spilled:
        return None
    return CompactReport(
        "micro_compact",
        f"落盘 {spilled} 项较早的工具结果（保留最近 {keep_recent} 条）",
        before,
        estimate_chars(messages),
    )


# ---------------- ④ compact_history ----------------


def compact_history(
    messages: list[dict[str, Any]],
    *,
    config: Config,
    chat: Any = chat_completion,
    limit: int = CONTEXT_CHAR_LIMIT,
) -> CompactReport | None:
    """整理之后仍然超限：存完整记录，用一次模型调用换摘要，替换历史。"""
    before = estimate_chars(messages)
    if before <= limit:
        return None

    transcript = _save_transcript(messages)
    summary = _summarize(messages, config=config, chat=chat)
    if summary is None:
        logger.warning("compact: 摘要生成失败，保留原历史")
        return None

    messages[:] = [{"role": "user", "content": _summary_message(summary, transcript)}]
    return CompactReport(
        "compact_history",
        f"摘要替换历史（完整记录 {transcript}）",
        before,
        estimate_chars(messages),
    )


# ---------------- ⑤ reactive_compact ----------------


def reactive_compact(
    messages: list[dict[str, Any]],
    *,
    config: Config,
    chat: Any = chat_completion,
    keep_recent: int = REACTIVE_KEEP_RECENT,
) -> CompactReport | None:
    """兜底：模型已经报超限，总结更早历史、保留最近若干条，供重试。"""
    before = estimate_chars(messages)
    tail_start = max(0, len(messages) - keep_recent)
    while tail_start > 0 and not _safe_boundary(messages, tail_start):
        tail_start -= 1

    earlier = messages[:tail_start]
    if not earlier:
        logger.warning("compact: 没有可总结的更早历史，兜底压缩放弃")
        return None

    transcript = _save_transcript(messages)
    summary = _summarize(earlier, config=config, chat=chat)
    if summary is None:
        return None

    tail = messages[tail_start:]
    messages[:] = [{"role": "user", "content": _summary_message(summary, transcript)}] + tail
    return CompactReport(
        "reactive_compact",
        f"摘要更早的 {len(earlier)} 条，保留最近 {len(tail)} 条",
        before,
        estimate_chars(messages),
    )
