"""ask_user: the only tool that asks the human a question.

With no channel (a non-interactive run, or a child without one) the model is told the
question cannot be asked and continues on existing information; an unanswered question
(timeout or cancel) gets the same note, worded as "no answer" rather than a refusal.
Concurrency is exclusive because two open questions split the interface and their answer
order means nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import tool

if TYPE_CHECKING:  # annotation only: tools must not depend on runtime at run time
    from ..state import RunState

#: Question, option-count and option-length caps; the interface mirrors these numbers.
MAX_QUESTION_CHARS = 500
MAX_OPTIONS = 6
MAX_OPTION_CHARS = 80

NO_CHANNEL = (
    "这个运行没有提问通道（非交互运行）。不要重复提问：基于现有信息继续，"
    "把不确定的地方写进结论，让下一步由人来定。"
)
NO_ANSWER = (
    "用户没有回答（超时或取消）。不要用同样的问题重复提问：基于现有信息继续，"
    "把不确定的地方写进结论。"
)


@tool(
    name="ask_user",
    description="向用户提问并等一个回答。用在「只有用户知道」的信息上：偏好、取舍、"
    "没有写在仓库里的约定。给 options 就变成选择题（界面渲染成按钮，用户仍可自由输入）。"
    "不要用它确认自己的工作计划、也不要问能从仓库/工具查到的信息；"
    "如果没人回答，你会收到一条「没有回答」的说明，此时应基于现有信息继续。",
    properties={
        "question": {
            "type": "string",
            "description": "要问的问题，一句话说清你需要什么（以及为什么需要）。",
        },
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选答案列表（最多 6 个）；给了就是选择题，不给就是自由回答。",
        },
    },
    required=("question",),
    concurrency="exclusive",
)
def ask_user(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    """Asks the human one question and returns their answer (or an explicit no-answer note)."""
    question = " ".join(str(args.get("question", "")).split())[:MAX_QUESTION_CHARS]
    if not question:
        return "错误：缺少参数 question"

    raw_options = args.get("options")
    options: tuple[str, ...] = ()
    if isinstance(raw_options, list):
        cleaned = [
            " ".join(str(item).split())[:MAX_OPTION_CHARS]
            for item in raw_options[:MAX_OPTIONS]
            if str(item).strip()
        ]
        options = tuple(cleaned)

    channel = getattr(state, "question", None)
    if channel is None:
        return NO_CHANNEL
    answer = channel(question, options)
    if answer is None or not str(answer).strip():
        return NO_ANSWER
    return f"用户回答：{str(answer).strip()}"


__all__ = ["MAX_OPTIONS", "MAX_QUESTION_CHARS", "NO_ANSWER", "NO_CHANNEL", "ask_user"]
