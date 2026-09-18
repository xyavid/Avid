"""上下文压缩管线：五步，代价由低到高。

每轮都跑（零 API 调用）：

* ① ``tool_result_budget`` —— 工具结果字符总量超预算就把当前最大的落盘
* ② ``snip_compact`` —— 消息条数超上限就裁掉中间，保留头尾

只在上下文超限时：

* ③ ``micro_compact`` —— 把较早的工具结果落盘，保留最近若干条，仍然不调模型
* ④ ``compact_history`` —— ③ 之后仍超限，才花一次模型调用换摘要，替换历史

兜底：

* ⑤ ``reactive_compact`` —— 模型报 ``prompt_too_long`` 时总结更早历史、保留最近若干条，重试一次

三条硬保证：①②③ 不调用模型；④ 每次运行最多一次（由调用方 ``RunState.compacted`` 决定）；
⑤ 最多一次。①②③ 的签名里没有 ``chat``，这在类型上就保证了它们碰不到模型 API。

五步都只通过 ``Transcript`` 的方法读写，因此结构不变量由所有者保证，
不需要"改完再事后校验"。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ai.config import Config
from ..ai.client import LLMError, chat_completion
from ..ai.transcript import Transcript

logger = logging.getLogger("avid.policy.compaction")

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


# ---------------- 落盘 ----------------


def _spill_root(root: Path | None = None) -> Path:
    """落盘目录：运行级工作区根优先，否则回落到进程默认根（调用时读取）。

    延迟导入 tools 是因为 agent 侧要 import 本模块，顶部导入会成环。
    """
    if root is not None:
        return Path(root) / SPILL_DIR
    from ..tools import workspace

    return Path(workspace.WORKSPACE_ROOT) / SPILL_DIR


def _spill(text: str, kind: str, root: Path | None = None) -> str | None:
    """写盘并返回工作区相对路径。压缩不是关键路径，落盘失败就跳过、不抛。"""
    global _spill_seq

    root = _spill_root(root)
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


def _is_spilled(content: str) -> bool:
    return content.startswith(SPILL_PREFIX)


def _save_transcript(messages: list[dict[str, Any]], workdir: Path | None = None) -> str:
    global _spill_seq

    root = _spill_root(workdir)
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


def _summarize(
    messages: list[dict[str, Any]], *, config: Config, chat: Any
) -> str | None:
    request = list(messages) + [
        {"role": "user", "content": "请把以上对话压缩成要点摘要。"}
    ]
    try:
        turn = chat(
            config, request, system=SUMMARY_SYSTEM, max_tokens=SUMMARY_MAX_TOKENS
        )
    except (LLMError, OSError, ValueError) as exc:
        # 摘要失败不该让整个运行崩掉：调用超限、网络断、响应不是合法 JSON 都算
        # "这一步没做成"，降级成保留原历史。**程序错误不许吞**——AssertionError /
        # TypeError / AttributeError 是代码 bug，吞掉只会把 bug 藏进压缩路径
        # （测试里的"不该调用模型"哨兵正是被原来那条 Exception 吞掉的）。
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
    transcript: Transcript,
    *,
    budget: int = TOOL_RESULT_CHAR_BUDGET,
    keep_recent: int = TOOL_RESULT_KEEP_RECENT,
    workdir: Path | None = None,
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
    before = transcript.tool_chars()
    if before <= budget:
        return None

    indexes = transcript.tool_indexes()
    if len(indexes) <= keep_recent:
        return None

    candidates = [
        (len(transcript.text_at(index)), index)
        for index in indexes[:-keep_recent]
        if not _is_spilled(transcript.text_at(index))
    ]
    if not candidates:
        return None

    size, index = max(candidates)
    if size == 0:
        return None

    path = _spill(transcript.text_at(index), "tool-result", workdir)
    if path is None:
        return None

    transcript.set_content(index, _notice(path, size, "工具结果"))
    return CompactReport(
        "tool_result_budget",
        f"落盘最大的一项工具结果（保留最近 {keep_recent} 条）",
        before,
        transcript.tool_chars(),
    )


# ---------------- ② snip_compact ----------------


def snip_compact(
    transcript: Transcript,
    *,
    max_messages: int = MAX_MESSAGES,
    keep_head: int = SNIP_KEEP_HEAD,
    keep_tail: int = SNIP_KEEP_TAIL,
) -> CompactReport | None:
    """消息条数超上限：裁掉中间，保留头尾。切口只在安全边界。"""
    before = len(transcript)
    if before <= max_messages:
        return None

    head_end = min(keep_head, before)
    tail_start = max(head_end, before - keep_tail)

    while head_end < tail_start and not transcript.is_safe_boundary(head_end):
        head_end += 1
    while tail_start > head_end and not transcript.is_safe_boundary(tail_start):
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
    transcript.splice(head_end, tail_start, [marker])
    return CompactReport("snip_compact", f"裁掉中间 {dropped} 条", before, len(transcript))


# ---------------- ③ micro_compact ----------------


def micro_compact(
    transcript: Transcript,
    *,
    limit: int = CONTEXT_CHAR_LIMIT,
    keep_recent: int = MICRO_COMPACT_KEEP_RECENT,
    target_ratio: float = MICRO_COMPACT_TARGET_RATIO,
    workdir: Path | None = None,
) -> CompactReport | None:
    """上下文超限：把较早的工具结果落盘，保留最近若干条。不调用模型。"""
    before = transcript.estimate_chars()
    if before <= limit:
        return None

    target = int(limit * target_ratio)
    indexes = transcript.tool_indexes()
    candidates = indexes[:-keep_recent] if keep_recent else indexes

    spilled = 0
    for index in candidates:
        if transcript.estimate_chars() <= target:
            break

        content = transcript.text_at(index)
        if _is_spilled(content):
            continue

        path = _spill(content, "tool-result", workdir)
        if path is None:
            break

        transcript.set_content(index, _notice(path, len(content), "工具结果"))
        spilled += 1

    if not spilled:
        return None
    return CompactReport(
        "micro_compact",
        f"落盘 {spilled} 项较早的工具结果（保留最近 {keep_recent} 条）",
        before,
        transcript.estimate_chars(),
    )


# ---------------- ④ compact_history ----------------


def compact_history(
    transcript: Transcript,
    *,
    config: Config,
    chat: Any = chat_completion,
    limit: int = CONTEXT_CHAR_LIMIT,
    workdir: Path | None = None,
) -> CompactReport | None:
    """整理之后仍然超限：存完整记录，用一次模型调用换摘要，替换历史。"""
    before = transcript.estimate_chars()
    if before <= limit:
        return None

    path = _save_transcript(transcript.as_messages(), workdir)
    summary = _summarize(transcript.as_messages(), config=config, chat=chat)
    if summary is None:
        logger.warning("compact: 摘要生成失败，保留原历史")
        return None

    transcript.replace_all(
        [{"role": "user", "content": _summary_message(summary, path)}]
    )
    return CompactReport(
        "compact_history",
        f"摘要替换历史（完整记录 {path}）",
        before,
        transcript.estimate_chars(),
    )


# ---------------- ⑤ reactive_compact ----------------


def reactive_compact(
    transcript: Transcript,
    *,
    config: Config,
    chat: Any = chat_completion,
    keep_recent: int = REACTIVE_KEEP_RECENT,
    workdir: Path | None = None,
) -> CompactReport | None:
    """兜底：模型已经报超限，总结更早历史、保留最近若干条，供重试。"""
    before = transcript.estimate_chars()
    messages = transcript.as_messages()

    tail_start = max(0, len(messages) - keep_recent)
    while tail_start > 0 and not transcript.is_safe_boundary(tail_start):
        tail_start -= 1

    earlier = messages[:tail_start]
    if not earlier:
        logger.warning("compact: 没有可总结的更早历史，兜底压缩放弃")
        return None

    path = _save_transcript(messages, workdir)
    summary = _summarize(earlier, config=config, chat=chat)
    if summary is None:
        return None

    tail = messages[tail_start:]
    transcript.replace_all(
        [{"role": "user", "content": _summary_message(summary, path)}] + tail
    )
    return CompactReport(
        "reactive_compact",
        f"摘要更早的 {len(earlier)} 条，保留最近 {len(tail)} 条",
        before,
        transcript.estimate_chars(),
    )
