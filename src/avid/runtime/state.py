"""一次运行的可变状态。

替代原来分散的 3 个 ContextVar 与 4 个循环局部变量：轮次计数、一次性标志、
统计，以及运行期实例（TODO 列表、技能注册表、免审批开关）。

由 ``agent_loop`` 创建并**显式传给各环节**；不跨运行共享，也不跨线程继承——
子 agent 自建一份，所以 ``--yes`` 这类运行级开关必须显式传过去（这正是它该有的样子：
传不过去会立刻报错，而不是静默失效）。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..ai.usage import Usage, hit_ratio
from ..policy.permission import (
    APPROVAL_NONE,
    DEFAULT_MODE,
    ApprovalLedger,
    RunSecurity,
    build_run_security,
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

# 连续这么多次工具调用被权限策略拒绝（期间**没有一次通过**）就停止整个运行。
# 判据是「被拒且毫无进展」，不是「拒绝总数」：任何一次成功调用都会把连击清零，
# 所以交替成功/被拒的正常任务永远踩不到这条线（现场会话 01a0d277 是同一份探针
# 被拒 15 次、15 轮里一次都没通过）。放在这里而不是策略层，理由同 TODO 阈值：
# 这是循环的节奏，循环取默认值时不必 import 策略层。
MAX_CONSECUTIVE_DENIALS = 5


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

    # 权限模式：三个预设之一（manual / auto / full）。默认 manual，
    # 因为默认值一放宽就是静默放大所有既有调用方的权限。
    permission_mode: str = DEFAULT_MODE

    # 运行级安全规格：三轴 + 四级 deny 阶梯 + 沙箱 + 审计。**唯一**的落点——
    # 工具执行时用的沙箱、事件里报的三轴、审计里记的裁决全部取自它，不各算一份。
    # 构造时没给就按 ``permission_mode`` + ``workspace_root`` 现算（见 __post_init__）。
    security: RunSecurity | None = None

    # full 的显式授权凭据：由 CLI/Web 的接线点传进来（`--allow-full-access` /
    # `full_access_ack=true`）。没有它就抛 FullAccessError——拒绝启动。
    full_ack: bool = False
    grant_source: str = "cli"

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
    #: 当前连击：从最后一次**通过**的调用算起，连续被拒了几次。
    denial_streak: int = 0
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

    # 并发写保护：一批工具调用可能同时在多个工作线程里跑（阶段 25），而下面几个
    # 计数是**读改写**——`+= 1` 与 `counts[key] = counts.get(key, 0) + 1` 在多线程下
    # 会丢更新。锁只保护这几个计数，其它字段仍是"循环线程写、工作线程读"。
    _counters_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

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

    def __post_init__(self) -> None:
        """把三轴解析成运行级安全规格。构造时没给就现算一份。

        为什么在构造时算而不是"用的时候再算"：``tools/shell.py`` 在工作线程里读它，
        懒算会把"第一次调用"变成一次隐式写入（并发下还得加锁）。构造点在循环之前，
        配置与探测都已完成，代价是一次缓存过的探测 + 两个小文件。
        """
        if self.security is None:
            self.security = build_run_security(
                mode=validate_mode(self.permission_mode),
                root=self.workspace_root,
                run_tag=self.run_tag,
                full_ack=self.full_ack,
                source=self.grant_source,
            )
        # 模式与规格必须一致：``security`` 是权威，``permission_mode`` 是它的名字。
        self.permission_mode = self.security.mode

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
        security: RunSecurity | None = None,
        full_ack: bool = False,
        grant_source: str = "cli",
        home: str | None = None,
        audit_dir: str | None = None,
        audit_enabled: bool = True,
    ) -> "RunState":
        """建一份运行状态，并**重新扫描一次技能目录**——磁盘变了，下次运行就生效。

        ``security`` 给了就沿用（子 agent 用父运行那一份：同一个沙箱、同一本审计、
        同一份阶梯）；没给就按模式现算。``full_ack`` 只有 CLI/Web 的接线点能填。
        """
        name = validate_mode(permission_mode or DEFAULT_MODE)
        built = security or build_run_security(
            mode=name,
            root=workspace_root,
            home=home,
            full_ack=full_ack,
            source=grant_source,
            audit_dir=audit_dir,
            audit_enabled=audit_enabled,
        )
        return cls(
            auto_approve=auto_approve,
            ask=ask,
            observer=observer,
            permission_mode=built.mode,
            security=built,
            full_ack=full_ack,
            grant_source=grant_source,
            ledger=ledger if ledger is not None else ApprovalLedger(),
            workspace_root=workspace_root,
            context_window=context_window,
            # 技能目录跟着运行级工作区根（没有工作区根时回落到 cwd）。
            skills=SkillLoader(default_skills_dir(workspace_root)).scan(),
            hooks=hooks if hooks is not None else hooks_module.DEFAULT_HOOKS,
        )

    # ---------------- 权限 ----------------

    def outside_allowed(self, path: object, access: str = "ro") -> bool:
        """文件工具据此判断越界目标是否已获授权。

        只读结果、不做决定：决定由 ``policy.permission.gate`` 做出并写进账本，
        所以"没有授权"必然失败关闭。``full``（approval=none）下整圈预授权。
        """
        if self.security is not None and self.security.approval == APPROVAL_NONE:
            return True
        return self.ledger.outside_allowed(path, access)

    def sandbox_grants(self) -> tuple[tuple[str, str], ...]:
        """本次运行已获准的区外路径（路径, ro/rw）。沙箱组装 argv 时读它。"""
        return self.ledger.path_grants()

    def security_summary(self) -> dict[str, Any]:
        """进事件与 REST 的三轴快照。"""
        if self.security is None:  # pragma: no cover - __post_init__ 保证非空
            return {}
        return self.security.summary()

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

    # ---------------- 并发安全的计数（阶段 25）----------------

    def note_tool_call(self) -> None:
        """记一次工具调用。批内并发时由多个工作线程调用，因此必须原子。"""
        with self._counters_lock:
            self.tool_calls += 1

    def note_denial(self) -> None:
        """记一次被拦截的调用。理由同上。连击 +1（见 MAX_CONSECUTIVE_DENIALS）。"""
        with self._counters_lock:
            self.denials += 1
            self.denial_streak += 1

    def note_allowed(self) -> None:
        """记一次**通过**权限的调用：连击清零。

        只用「有没有被拒」判连击，不看工具是否执行成功——工具自己报错是模型看得见的
        信息，属于有进展；被拒才是原地打转。
        """
        with self._counters_lock:
            self.denial_streak = 0

    def note_repeat(self, key: str) -> int:
        """同一个键的出现次数（从 1 开始）。读改写在一把锁里完成。

        返回自己这一次的序号：并发下若丢号，重复提醒（第 3、5 次）就永远到不了阈值——
        这是"看起来只是少一句话"、实际会让提醒彻底失效的那类丢更新。
        """
        with self._counters_lock:
            times = self.repeat_calls.get(key, 0) + 1
            self.repeat_calls[key] = times
            return times

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
            "denial_streak": self.denial_streak,
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
