"""一次运行的可变状态。

替代原来分散的 3 个 ContextVar 与 4 个循环局部变量：轮次计数、一次性标志、
统计，以及运行期实例（TODO 列表、技能注册表、免审批开关）。

由 ``agent_loop`` 创建并**显式传给各环节**；不跨运行共享，也不跨线程继承——
子 agent 自建一份，所以 ``--yes`` 这类运行级开关必须显式传过去（这正是它该有的样子：
传不过去会立刻报错，而不是静默失效）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..ai.usage import Usage, hit_ratio
from ..policy.permission import (
    DEFAULT_MODE,
    MODE_SYSTEM,
    ApprovalLedger,
    validate_mode,
)
from ..policy.skills import SkillLoader, default_skills_dir
from ..policy.todo import TodoList, build_reminder
from . import hooks as hooks_module
from .events import RunObserver, event

if TYPE_CHECKING:  # 与 loop.py 同理：AskUser 只出现在注解里
    from ..policy.permission import AskUser

# TODO 提醒阈值：连续多少轮没更新就提醒一次。
# 它是**运行级配置**（循环的节奏）而不是策略层的规则，所以放这里——
# 循环取默认值时不必 import 策略层。
TODO_REMINDER_AFTER_ROUNDS = 3


def _split_context(
    tokens: int | None, chars: tuple[int, int, int] | None
) -> dict[str, int] | None:
    """把真实的 ``prompt_tokens`` 按三块（system / tools / messages）的**字符占比**分配。

    为什么不用"每 token 多少字符"的绝对系数：那类系数在中英混排下必然偏（同样字符数的
    英文与中文 token 数差好几倍），而占比只用三块之间的**相对**量，误差小得多。
    三块之和**恰好等于** ``tokens``（余数给对话消息），于是界面上的堆叠条与总数永远
    对得上——前端会给每块加 `~` 并注明这是估算。

    缺任一前提（没有真实总数、或这一轮没记字符数）就返回 ``None``：不猜。
    """
    if tokens is None or chars is None:
        return None
    total = sum(chars)
    if total <= 0:
        return None
    system, tools, _ = chars
    system_tokens = tokens * system // total
    tools_tokens = tokens * tools // total
    return {
        "system": system_tokens,
        "tools": tools_tokens,
        "messages": tokens - system_tokens - tools_tokens,
    }


@dataclass
class RunState:
    # 运行级开关
    auto_approve: bool = False

    # 权限模式：名字即信任边界（strict / workspace / system）。默认最严，
    # 因为默认值一放宽就是静默放大所有既有调用方的权限。
    permission_mode: str = DEFAULT_MODE

    # "同意一次即生效"的账本：只活一次运行、只在内存里。子 agent 与父 agent 共用一本。
    ledger: ApprovalLedger = field(default_factory=ApprovalLedger)

    # 本次运行的工作区根。None 表示"由 tools.workspace.WORKSPACE_ROOT 决定"——
    # **在调用时读取**，而不是在 import 时拷进默认值，否则测试的 monkeypatch 会失效。
    workspace_root: str | None = None

    # 策略注入点：审批回调（None = 回落到 policy.permission.ask_user）。
    # Web 路径注入自己的实现，于是审批不再读 stdin（设计文档 §7.2）。
    ask: AskUser | None = None

    # 事件观察者：None 表示这次运行没有订阅者（CLI 不建事件流）。
    observer: RunObserver | None = None

    # 取消：由另一个线程置位，循环在两个检查点读取（设计文档 §7.4）。
    cancelled: bool = False
    cancel_reason: str | None = None

    # 轮次与终止
    round: int = 0
    rounds_since_todo: int = 0
    stop_blocks: int = 0

    # 一次性标志：谁负责"整个运行最多一次"
    compacted: bool = False
    retried: bool = False

    # 统计：进 Stop 事件，供 hook 与测试观察
    tool_calls: int = 0
    denials: int = 0
    compactions: int = 0
    # 累计用量：整个运行所有轮次 total_tokens 之和。它**不是**上下文占用——
    # 占用看下面的 `last_usage.prompt_tokens`（同一份上下文会被反复计费）。
    tokens: int = 0

    # ---------------- usage 台账（阶段 22）----------------
    #
    # 上下文记账归运行状态自己掌握：循环每轮只把 provider 的 `Usage` 交进来一次，
    # 「占用多少 / 命中率多少 / 压缩后是多少」全在这里算，事件、REST 与落盘因此同源。
    # 字段一律可空：`None` 表示"没有这个数"，界面显示「—」，不用 0 冒充。

    # 最近一轮模型调用的用量（含 prompt_tokens 与缓存读写）。
    last_usage: Usage | None = None
    # 模型上下文窗口。None = 不认识这个模型（占用率不可计算）。
    context_window: int | None = None
    # 最近一次压缩之后**下一轮真实读数**：压缩省了多少只有模型说了算。
    last_compaction_tokens: int | None = None
    # 那一次压缩是哪一步（micro_compact / compact_history …），供明细显示。
    last_compaction_step: str | None = None
    # 已经压过、还没等到下一轮读数：由 `mark_compacted` 置位，`record_usage` 消费。
    compact_pending: bool = False
    # 这次请求的三块文本各占多少**字符**（system / tools / messages），由循环在真正
    # 发请求前记下。字符不是 token：它只用来把真实 `prompt_tokens` 按占比分给三块
    # （`_split_context`），所以这里存事实、不做换算。
    prompt_parts: tuple[int, int, int] | None = None

    # 同名同参工具的重复次数（键是 `名字:规范化参数`），由 `repeat_call_hook` 读写。
    # 挂在运行状态上而不是回调闭包里：一次运行一份，新的用户输入换一份新的 RunState，
    # 计数因此自然清零；回调本身仍是可共享的纯函数。
    repeat_calls: dict[str, int] = field(default_factory=dict)

    # 本次运行的短标识：压缩落盘的文件名里带上它，否则两次运行（哪怕进程重启后）
    # 会写同一个 `tool-result-0001.txt`，把上一次的上下文记录静默覆盖掉。
    run_tag: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # 运行期实例
    todo: TodoList = field(default_factory=TodoList)
    skills: SkillLoader = field(default_factory=SkillLoader)
    # hook 注册表归运行所有（以前是 `hooks.HOOKS` 模块级字典）：`agent_loop(hooks=…)`
    # 能注入一份，不注入就用进程级默认。子 agent 用父运行那一份（`copy()`）。
    # 通过模块取默认值（而不是 `from .hooks import DEFAULT_HOOKS`）：默认注册表只有
    # 一个拥有者，替换 `hooks.DEFAULT_HOOKS` 就必须生效——`from ... import` 会把名字
    # 绑进本模块的命名空间，替换源头反而不生效（这个坑当场被 25 个用例照出来）。
    hooks: "hooks_module.HookRegistry" = field(
        default_factory=lambda: hooks_module.DEFAULT_HOOKS
    )

    @classmethod
    def for_run(
        cls,
        *,
        auto_approve: bool = False,
        ask: AskUser | None = None,
        observer: RunObserver | None = None,
        permission_mode: str | None = None,
        ledger: ApprovalLedger | None = None,
        workspace_root: str | None = None,
        hooks: "hooks_module.HookRegistry | None" = None,
        context_window: int | None = None,
    ) -> "RunState":
        """建一份运行状态，并**重新扫描一次技能目录**——磁盘变了，下次运行就生效。"""
        return cls(
            auto_approve=auto_approve,
            ask=ask,
            observer=observer,
            permission_mode=validate_mode(permission_mode or DEFAULT_MODE),
            ledger=ledger if ledger is not None else ApprovalLedger(),
            workspace_root=workspace_root,
            context_window=context_window,
            # 技能目录跟着运行级工作区根（没有工作区根时回落到 cwd）。
            skills=SkillLoader(default_skills_dir(workspace_root)).scan(),
            hooks=hooks if hooks is not None else hooks_module.DEFAULT_HOOKS,
        )

    # ---------------- 权限 ----------------

    def outside_allowed(self, path: object) -> bool:
        """文件工具据此判断越界目标是否已获授权。

        只读结果、不做决定：决定由 ``policy.permission.gate`` 做出并写进账本，
        所以"没有授权"必然失败关闭。``system`` 模式下整圈预授权。
        """
        if self.permission_mode == MODE_SYSTEM:
            return True
        return self.ledger.outside_allowed(path)

    # ---------------- 事件与取消 ----------------

    def emit(self, type: str, **data: Any) -> None:
        """把一条步骤级事实交给观察者。

        内核只描述事实，不分配 seq、不知道 run_id——那是 svc 的事。没有观察者
        时是零开销的 no-op（CLI 路径）。
        """
        if self.observer is not None:
            self.observer(event(type, **data))

    def cancel(self, reason: str | None = None) -> None:
        """请求取消。只置位，不打断当前步骤——粒度写进 UI 文案（§7.4）。"""
        self.cancelled = True
        self.cancel_reason = reason or "cancelled"

    def check_cancelled(self) -> None:
        """循环的两个检查点调用它；命中抛 ``RunCancelled``。"""
        if self.cancelled:
            from .loop import RunCancelled

            raise RunCancelled(self.cancel_reason or "cancelled")

    def system_prompt(self, instructions: str | None = None) -> str:
        """固定指令 + 环境信息 + 技能目录。

        ``instructions`` 为 None 时用策略层的默认指令——于是循环不必知道那段默认文案。
        工作目录取运行级根：模型看到的路径必须与实际解析用的根一致。
        """
        if instructions is None:
            return self.skills.build_system_prompt(workdir=self.workspace_root)
        return self.skills.build_system_prompt(instructions, workdir=self.workspace_root)

    def snapshot(self) -> dict[str, int]:
        """Stop 事件要看到的运行统计。"""
        return {
            "rounds": self.round,
            "tool_calls": self.tool_calls,
            "denials": self.denials,
            "compactions": self.compactions,
        }

    # ---------------- usage 台账 ----------------

    def record_usage(self, usage: Usage) -> None:
        """记一轮模型调用的用量。**唯一**写这些字段的地方（循环只调它一次）。

        压缩后的读数在同一处回填：``mark_compacted`` 之后的第一轮 ``prompt_tokens``
        正是"压完还剩多少"，不需要另做一次本地估算——估算会与计费口径打架。
        """
        self.tokens += usage.total_tokens
        self.last_usage = usage
        if self.compact_pending:
            self.last_compaction_tokens = usage.prompt_tokens
            self.compact_pending = False

    def mark_compacted(self, step: str) -> None:
        """一次压缩真的发生了（``policy/compaction`` 给出了报告）。

        压缩计数与"等下一轮读数"一起记：两者必须同时发生，所以只有一个入口。
        """
        self.compactions += 1
        self.last_compaction_step = step
        self.compact_pending = True

    def record_prompt_parts(self, *, system: int, tools: int, messages: int) -> None:
        """记下这次请求三块文本的字符数（循环在发请求前调用）。

        正文之外的两块（系统提示、工具定义）只在循环里可得——它们从不发给前端，
        所以"上下文被谁占了"必须由内核自己算并随快照带出去。
        """
        self.prompt_parts = (max(0, system), max(0, tools), max(0, messages))

    def usage_report(self) -> dict[str, Any]:
        """统一 usage schema —— 事件、REST 与落盘共用这一个计算点。

        形状（阶段 22 与前端、与落盘一致）::

            {"context": {"tokens", "window", "utilization", "parts"},
             "cache": {"read_tokens", "write_tokens", "hit_ratio"},
             "compaction": {"count", "last_compaction_tokens", "last_step"}}

        ``context.parts`` 是三块文本的**估算** token（按字符占比分配真实总数，
        见 `_split_context`）：``None`` = 没有这一轮的分块数据。

        可空字段一律 ``None`` 表示"没有这个数"：窗口不认识该模型、这次上报里没有
        用量、这家没有写入缓存的计数——三种情况界面都显示「—」，绝不用 0 冒充。
        """
        usage = self.last_usage
        if usage is not None and usage.prompt_tokens <= 0 and usage.total_tokens <= 0:
            # 全零 = 这次没有用量上报（端点不认 `stream_options.include_usage`）；
            # 真实请求不可能一个输入 token 都没有，所以它与"用了 0 个"不同。
            usage = None
        prompt = None if usage is None else usage.prompt_tokens
        window = self.context_window
        return {
            "context": {
                "tokens": prompt,
                "window": window,
                "utilization": (
                    prompt / window if prompt is not None and window else None
                ),
                "parts": _split_context(prompt, self.prompt_parts),
            },
            "cache": {
                "read_tokens": None if usage is None else usage.cache_read_tokens,
                "write_tokens": None if usage is None else usage.cache_write_tokens,
                "hit_ratio": None if usage is None else hit_ratio(usage),
            },
            "compaction": {
                "count": self.compactions,
                "last_compaction_tokens": self.last_compaction_tokens,
                "last_step": self.last_compaction_step,
            },
        }

    def todo_reminder(self, threshold: int) -> str | None:
        """连续 threshold 轮没更新 TODO 时给出提醒文本，否则 None。

        "该不该提醒"与"提醒什么"都在这里，循环只负责把它追加进消息——
        于是循环不必 import 策略层（原本它要拿阈值常量与文案函数）。
        """
        if self.rounds_since_todo != threshold:
            return None
        return build_reminder(self.todo, self.rounds_since_todo)
