# Agent Runtime 架构设计

> 状态：设计稿。本文只**重组既有能力**，不引入新功能——所有被参考的方案里属于新能力的部分，都在 §8 逐条写明"为什么不跟"。
> 推导依据：`docs/design/architecture-criteria.md` 的 12 组检查点。覆盖情况见 §11，未验证的显式标注。

## 1. 变化优先：先定位会变的东西

判据 §1 要求先列变化、标注频率与代价，再看它现在迫使谁跟着改。

| 变化点 | 频率 | 代价 | 现在被迫跟着改的模块 | 是否合理 |
|---|---|---|---|---|
| 压缩阈值与步骤顺序 | 高（阶段 8 一次改动就动了 `agent.py` 73 行） | 低 | `agent.py` 的循环体 | ✗ 不该 |
| TODO 提醒阈值与文案 | 中 | 低 | `agent.py`（计数与注入都写在循环里） | ✗ 不该 |
| 技能目录结构与 system prompt 组装 | 中 | 低 | `agent.py`（loader 创建与 prompt 拼装都在循环前） | ✗ 不该 |
| 工具集增删 | 中（阶段 2 / 5 / 6 / 7 各加过一次） | 中 | `tools/` + `agent.py` 的 `_execute_one` | ✗ 部分不该 |
| 每加一个"整轮最多一次"的机制 | 中（`compacted` / `retried` / `stop_blocks` 各加过一次） | 低 | `agent.py` 新增一个局部标志 | ✗ 不该 |
| 权限规则 | 中 | 低 | `permission.py` | ✓ 已隔离 |
| 模型协议 / provider 措辞 | 低 | 高 | `llm.py` | ✓ 已隔离 |
| 工具实现内部 | 高 | 低 | `tools/` | ✓ 已隔离 |
| 并行工具执行 / 流式输出 | 低 | 高 | 无（未做） | — |

**结论**：变化集中在两类东西上——**策略值**（阈值、文案、规则）与**编排顺序**（每轮做什么、什么条件做什么）。这两类现在都落在 `agent.py` 里。

### 1.1 现状耦合的量化证据

- `src/avid/agent.py` 共 **324 行**，其中 **35 行**提到某个策略的具体名字（`compact` / `todo` / `skill` / `permission`）。
- 同文件里有 **5 处**直接写 `messages`：用户输入注入（L212）、TODO 提醒（L232）、assistant 追加（L275）、Stop nudge（L305）、工具结果扩展（L313）。
- 运行状态通过 **3 个 contextvars** 隐式持有：`RUN_AUTO_APPROVE`（`permission.py`）、`CURRENT`（`tools/todo.py`）、`CURRENT`（`skill_loader.py`）；另有 4 个循环局部标志（`rounds_since_todo` / `compacted` / `retried` / `stop_blocks`）。

判据 §3 把"共享可变状态、隐式调用、全局单例"列为要检查的耦合机制——这三条都命中了。

## 2. 具体先行：抽象准入与删除测试

判据 §2 要求每个新抽象指名证据类型（真实重复 / 真实变化 / 真实耦合），并做删除测试。

| 拟建边界 | 准入证据 | 删除测试结果 |
|---|---|---|
| `Transcript`（messages 所有者） | **真实耦合**：5 处直接写 + 结构不变量只能事后 `validate_structure` 检查 | 删掉后 5 处重新耦合、不变量退回事后校验 → **保留** |
| `runtime/context.py`（压缩编排） | **真实变化**：阶段 8 改过一次编排（加 ⑤、改 ① 语义），每次都动循环 | 删掉后压缩顺序回到循环，改阈值要动循环 → **保留** |
| `runtime/execution.py`（工具执行环节） | **真实变化**：4 次加工具；**真实耦合**：参数解析/查找/拦截/执行/回写挤在一个函数里 | 删掉后加工具要同时改 `tools/` 与循环 → **保留** |
| `RunState`（显式运行状态） | **真实耦合**：3 个 contextvars + 4 个局部标志 | 删掉后退回隐式全局状态 → **保留** |
| `policy/` 目录分包 | **真实变化**：阈值与文案是高频变化 | 删掉（文件位置不动）后逻辑分层仍在，只失去"目录即边界"的可见性 → **可删** |
| 事件总线从 hooks 改名 | 无（`hooks.py` 已经是对的边界） | 改名不带来隔离增益 → **不做**（只保留文件位置与签名） |

最后两行是本文档最重要的两个"不做"结论：**分包是可删的**（所以放到第二阶段，见 §10），**hooks 不需要改**（避免为改而改）。

## 3. 目标分层与模块职责

四层单向向下，依赖方向由 §12 的断言守住。

| 层 | 模块 | 职责 | 它隔离了什么（判据 §4） | 依赖 |
|---|---|---|---|---|
| 应用 | `cli.py` | 参数解析、进程退出码、stdout/stderr 格式 | 隔离"交互形态"：改 CLI 不该动运行时 | runtime |
| 运行时 | `runtime/loop.py` | **只表达调度顺序**：一轮里先做什么、什么条件做什么 | 隔离"轮次"这个概念本身 | runtime 其它 + ai + tools |
~~`runtime/transcript.py`~~ → 见下方修正：**`ai/transcript.py`**。`messages` 的**唯一所有者**；写入时保证结构不变量；字符估算。工具调用与结果的配对是**协议要求**（OpenAI 兼容端点会拒绝不配对请求），不是运行时策略，所以归协议层 | 隔离"消息结构合法性" | 无（纯数据结构） |
| 运行时 | `runtime/context.py` | 上下文管线的**编排**：调用哪些压缩步骤、什么顺序、什么条件 | 隔离"上下文策略的组合方式" | policy.compaction |
| 运行时 | `runtime/execution.py` | 工具执行环节：解析参数 → 拦截 → 执行 → 回填 | 隔离"工具调用协议" | events / tools（**不含 policy**：权限判定在 `runtime/hooks.py` 触发的回调里，见 §12 判据 9） |
| 运行时 | `runtime/state.py` | `RunState`：轮次计数、一次性标志、计数统计、TODO 与技能实例 | 隔离"运行期可变状态的生命周期" | policy.todo / policy.skills |
| 运行时 | `runtime/hooks.py` | 事件注册与触发（**文件名与签名都不变**——§2 的删除测试判定改名无收益） | 隔离"扩展点的发现方式" | 无 |
| 策略 | `policy/permission.py` | 三闸门 + 黑名单 + 审批 | 隔离"安全规则" | 无 |
| 策略 | `policy/compaction.py` | 五步压缩的**实现**与阈值常量 | 隔离"阈值与压缩算法" | 无（纯函数） |
| 策略 | `policy/todo.py` | `TodoList` 状态模型与更新规则 | 隔离"计划的数据结构" | 无 |
| 策略 | `policy/skills.py` | 技能扫描、目录、全文读取 | 隔离"技能来源与解析" | 无 |
| 协议 | `ai/client.py` | `/chat/completions`、`Turn`、错误分类 | 隔离"HTTP 与各家措辞" | 无 |
| 协议 | `ai/config.py` | 环境变量读取 | 隔离"配置来源" | 无 |
| 能力 | `tools/*` | 14 个工具的实现与 schema | 隔离"文件系统与进程" | 无（`subagent` 例外见 §5.3） |

不新增层、不新增能力。分层只是在当时的 13 个模块上重排依赖方向（现在模块更多，分层不变；计数以 `pkgutil.walk_packages` 实测为准）。

## 4. 核心接口与数据结构

### 4.1 `Transcript` —— messages 的唯一所有者

```python
class Transcript:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None: ...

    def as_messages(self) -> list[dict[str, Any]]:
        """只读用途：传给模型调用。返回浅拷贝，调用方改它不影响内部状态。"""

    def append(self, message: dict[str, Any]) -> None: ...
    def append_many(self, messages: Iterable[dict[str, Any]]) -> None: ...

    def replace_all(self, messages: list[dict[str, Any]]) -> None:
        """整体替换（④ compact_history 用）。替换前校验结构，非法则抛 TranscriptError。"""

    def splice(self, start: int, stop: int, replacement: list[dict[str, Any]]) -> None:
        """区间替换（② snip_compact 用）。start/stop 必须落在安全边界上，否则抛错。"""

    def set_content(self, index: int, content: str) -> None:
        """改单条内容（tool_result 落盘留路径、用户输入注入用）。不改结构，无需校验。"""

    def last_user_index(self) -> int | None: ...
    def tool_indexes(self) -> list[int]: ...
    def is_safe_boundary(self, index: int) -> bool: ...
    def validate(self) -> list[str]: ...          # 原 compact.validate_structure
    def estimate_chars(self) -> int: ...          # 原 compact.estimate_chars
```

要点：
- `replace_all` / `splice` 是**唯一**能改变结构的方法，它们内部先构造候选、校验通过才落地。这满足判据 §6「不变量要有唯一守护者」。
- `set_content` 不校验，因为它不可能破坏配对关系——把校验成本放在真正需要的地方。

### 4.2 `RunState` —— 显式运行状态

```python
@dataclass
class RunState:
    # 轮次与终止
    round: int = 0
    rounds_since_todo: int = 0
    stop_blocks: int = 0
    # 一次性标志（判据 §6：谁负责"最多一次"）
    compacted: bool = False
    retried: bool = False
    # 统计（进 Stop 事件，供 hook 与测试观察）
    tool_calls: int = 0
    denials: int = 0
    compactions: int = 0
    # 运行期实例（原来是 3 个 contextvars）
    todo: TodoList = field(default_factory=TodoList)
    skills: SkillLoader = field(default_factory=SkillLoader)
    auto_approve: bool = False
```

`RunState` 由 `agent_loop` 创建、传给各环节、随运行结束而丢弃。取代表：

| 原机制 | 现在 | 变化 |
|---|---|---|
| `contextvars.ContextVar`（3 个） | 数据类字段 | 隐式 → 显式 |
| 循环局部变量（4 个） | 数据类字段 | 无法被 hook 观察 → 可观察 |

**替代 contextvars 的代价**：`todo_write` / `load_skill` 的 handler 签名是 `Callable[[dict], Any]`，它拿不到 `RunState`。两条路：

1. `run_subagent` 在子线程里自建 `RunState`（现在也是自建 context；改成显式后代码更直白）。
2. `ToolImpl` 签名不变，改为在 `execution.execute_batch` 里把 `RunState` 放进工具调用上下文，由 `execution` 负责传参——即工具的 handler 多一个可选参数。

**选定方案 2，落地时逐步扩到全部工具**：初稿只给 `todo_write`、`load_skill`、`subagent`
三个工具传 `state`；到阶段 18，"越界检查要工作区根、审批要账本、任务工具要工作区根"把所有工具
都拉了进来，于是 `STATEFUL_TOOLS` == `TOOLS`（14 个），签名统一 `(args, *, state)`。集合仍然
显式可枚举（判据 §4：谁拥有状态要能指名），契约测试保证不漏不错。

> 落地时的修正：初稿只列了两个工具，漏了 `subagent`——它也要读 `RunState.auto_approve`（原来是 `RUN_AUTO_APPROVE` 这个 ContextVar）。当时是 **3 / 8**；阶段 18 之后统一成 14 / 14（见上）。该集合落在 `execution.STATEFUL_TOOLS`，由契约测试保证不漏不错。

**这个代价值不值**：值得，因为它是唯一能在不引入依赖注入框架的前提下消除 contextvars 的办法；
不值的信号是"需要的工具超过一半"——**这个信号在阶段 18 出现了**（14 / 14），于是按当初写下的
判断统一传 `state`，没有回头改判据。

### 4.3 `runtime/context.py` —— 压缩编排

```python
@dataclass(frozen=True)
class ContextBudget:
    tool_result_chars: int = 200_000
    max_messages: int = 50
    keep_head: int = 8
    keep_tail: int = 24
    context_chars: int = 400_000
    micro_keep_recent: int = 3
    micro_target_ratio: float = 0.8
    reactive_keep_recent: int = 5

@dataclass(frozen=True)
class Preparation:
    reports: list[CompactReport]

def prepare(
    transcript: Transcript,
    state: RunState,
    *,
    config: Config,
    summarize: Callable[..., Turn],   # 只有 ④ 会用；①②③ 拿不到它
) -> Preparation: ...
```

- `prepare` 是**唯一**知道"①② 每轮跑、③④ 有条件"的地方。循环只调它一次。
- `summarize` 作为参数传入而不是让 `prepare` 自己 import：这从**类型上**保证了 ①②③ 无法触达模型（判据 §6 不变量 I4）。原实现靠 `inspect.signature` 事后断言，现在由签名保证。
- `compact_history` 的"整轮最多一次"由 `state.compacted` 决定，但**只有 `prepare` 写这个字段**——循环不改它。

### 4.4 `runtime/execution.py` —— 工具执行环节

```python
@dataclass(frozen=True)
class ToolOutcome:
    tool_call_id: str
    content: str

def execute_batch(
    tool_calls: list[dict[str, Any]],
    *,
    state: RunState,
    registry: dict[str, ToolImpl],
    round_index: int,
) -> list[ToolOutcome]: ...
```

内部顺序与现在完全一致（这是行为不变的关键）：

```
for call in tool_calls:
    name, raw_args = 解析 call
    args = json.loads(raw_args)          # 失败 → content = "参数不是合法 JSON：…"
    if not isinstance(args, dict):       # → content = "错误：参数必须是 JSON 对象"
    impl = registry.get(name)            # 缺失 → content = "未知工具：…"
    state.tool_calls += 1
    before = {tool, arguments, round}
    if trigger_hooks("PreToolUse", before) == BLOCK:
        state.denials += 1
        content = before.get("denied_content") or DENIED_CONTENT
    else:
        content = _as_text(impl(args, state=state) if name in STATEFUL_TOOLS else impl(args))
    after = {tool, arguments, round, content, truncated: False}
    trigger_hooks("PostToolUse", after)
    yield ToolOutcome(call_id, after["content"])
```

注意所有失败仍**返回文本而不是抛异常**——这是本项目既有约定（见 §8 与 pi 的差异）。

### 4.5 `runtime/loop.py` —— 只剩调度

```python
def agent_loop(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools=None, registry=None, config=None, chat=chat_completion,
    auto_approve: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_rounds: int = MAX_ROUNDS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    todo_reminder_after: int = TODO_REMINDER_AFTER_ROUNDS,
) -> str:
```

**公开签名一字不改**——现有 60+ 处测试调用点因此不用改。这是重构"行为不变"最直接的证据形式。

> 阶段 16（F3）在 `chat` 之后补了一个 `summarize=None`（默认回落到 `chat`，所以不改变任何
> 现有调用点）：流式只该包住**主轮次**，而压缩摘要是通过同一个 `chat` 调用的；不把两者
> 分开，摘要文本就会作为 delta 推给前端，与真正的回复粘成同一条气泡。`context.prepare` /
> `context.reactive` 收到的仍是本文件 §4 那个 `summarize`，签名与不变量 I4 因此不变。

## 5. 运行时数据流与调度

### 5.1 一轮的时序

```
agent_loop(messages, ...)
├─ transcript = Transcript(messages)            # 接管调用方传进来的 list（同一对象，原地语义保持）
├─ state = RunState(skills=SkillLoader().scan(), auto_approve=auto_approve, todo=TodoList())
├─ system_prompt = state.skills.build_system_prompt(system or AGENT_INSTRUCTIONS)
├─ emit UserPromptSubmit(transcript.last_user_index())
│    ├─ block → 返回 ""
│    └─ injected → transcript.set_content(last_user_index, 注入 + 原文)
└─ for round in 1..max_rounds:
     ├─ state.round = round
     ├─ if state.rounds_since_todo == todo_reminder_after:
     │      transcript.append(提醒)              # 依赖轮次，留在循环里（判据 §2：它确实是循环的事实）
     ├─ context.prepare(transcript, state, config=config, summarize=chat)
     │    ├─ ① tool_result_budget     （每轮）
     │    ├─ ② snip_compact           （每轮）
     │    ├─ ③ micro_compact          （超限时）
     │    └─ ④ compact_history        （仍超限 且 未用过 → 置 state.compacted）
     ├─ turn = call_model(transcript, ...)       # ⑤ 包在这层：PromptTooLongError → reactive → 重试一次
     ├─ transcript.append(turn.message)
     ├─ state.rounds_since_todo = 0 if 本轮有 todo_write else +1
     ├─ if not turn.tool_calls:
     │    ├─ emit Stop(state 快照)
     │    ├─ block 且 state.stop_blocks < max_stop_blocks → 追加 nudge、continue
     │    └─ 否则 → return turn.text
     └─ outcomes = execution.execute_batch(turn.tool_calls, state=state, ...)
        transcript.append_many(outcomes → tool 消息)
   raise RoundLimitExceeded
```

### 5.2 谁在什么时候读 `Transcript`

| 阶段 | 读 | 写 | 写的方法 |
|---|---|---|---|
| 启动 | `last_user_index` | 注入上下文 | `set_content` |
| ① | `tool_indexes` | 落盘留路径 | `set_content` |
| ② | 长度、边界 | 裁中间 | `splice` |
| ③ | 字符估算、`tool_indexes` | 落盘留路径 | `set_content` |
| ④ | 全部 | 摘要替换 | `replace_all` |
| ⑤ | 全部 | 摘要 + 保留尾部 | `replace_all` |
| 模型轮 | `as_messages` | 追加 assistant | `append` |
| 工具轮 | — | 追加 tool 结果 | `append_many` |
| Stop | — | 追加 nudge | `append` |

九处读写全部经过 5 个写入方法（`append` / `append_many` / `set_content` / `replace_all` / `splice`）。**没有任何一处再直接操作 list**。

### 5.3 subagent 的位置

`tools/subagent.py` 的 `run_subagent` 自己构造 `RunState` 并调用 `agent_loop`——与现在自建 TodoList/loader 的做法同构，只是从"绑定 contextvar"变成"构造对象"。子线程不继承 `RunState` 这一点从"隐式失效"变成"签名上看得见"（它必须显式接收 `auto_approve`）。

## 6. 不变量与守护者

判据 §6 要求先列不变量，再指派唯一守护者并检查是否有旁路。

| # | 不变量 | 守护者 | 旁路检查 |
|---|---|---|---|
| I1 | 每个 assistant 的 `tool_calls` 都有配对的 `tool` 结果 | `Transcript` | 唯一的写入方法是 `append* / splice / replace_all`，都在内部校验 |
| I2 | system prompt 不写进 `messages` | `loop`（只有它构造 `system=` 参数） | 无其他模块构造该参数 |
| I3 | 压缩前后结构合法 | `Transcript.replace_all / splice` 先校验后落地 | 压缩只能通过这些方法改结构 |
| I4 | ①②③ 不触达模型 API | `context.prepare` 的签名（`summarize` 只传给 ④） | 类型上不可达，无需运行时检查 |
| I5 | 自动压缩 ≤1 次、兜底 ≤1 次 | `RunState.compacted` / `RunState.retried`，只有 `prepare` 与 `loop` 写 | grep 断言这两个字段的赋值点各只有一处 |
| I6 | 工具失败不中断循环 | `execution.execute_batch`（失败转文本） | 仅有此一处执行工具 |

## 7. 失败与恢复

判据 §7 要求失败路径与正常路径同等级设计。下表的机制**全部是既有实现**，重构不得削弱：

| 类型 | 场景 | 现行机制 | 重构后位置 |
|---|---|---|---|
| 临时 | 模型超时 / 连接断 | `httpx` 超时 + `LLMError` | `ai/client.py`（不变） |
| 临时 | 上下文超限 | `PromptTooLongError` → ⑤ 兜底 + 重试一次 | `loop.call_model` |
| 永久 | 其它 4xx / 5xx | `LLMError` 上抛，运行终止 | `ai/client.py` + `cli.py` 退出码 |
| 业务拒绝 | 权限三闸门拒绝 | 回文本 `Permission denied. …`，循环继续 | `execution.py` |
| 数据错误 | 工具参数非法 / 未知工具 | 回文本，不触发事件 | `execution.py` |
| 环境错误 | 落盘失败（压缩） | 记日志、跳过本次压缩 | `policy/compaction.py` |
| 环境错误 | 摘要调用失败 | 记日志、保留原历史 | `policy/compaction.py` |
| 程序错误 | hook 抛异常 | 按 block 处理（失败关闭），不影响其它 hook | `runtime/hooks.py` |
| 未收敛 | 轮数耗尽 | `RoundLimitExceeded` | `loop.py` |

**不做**（pi 有而我们没有，且属于新功能）：崩溃恢复 / checkpoint / 重放、失败重试队列、补偿操作。

## 8. 与 pi 的异同

参考对象：`earendil-works/pi`（`pi-ai` / `pi-agent-core` / `pi-coding-agent` 三个包，运行时在 `packages/agent/src/harness/`）。以下基于其 README 与仓库文件树，未逐行读实现——标注为**未验证假设**的部分见 §11。

### 8.1 相同或同构（5 条）

| # | pi 的做法 | 我们的对应 | 说明 |
|---|---|---|---|
| S1 | 三层包：`pi-ai` → `pi-agent-core` → `pi-coding-agent` | `ai/` → `runtime/` → `cli.py` | 依赖单向向下，协议层不反向依赖运行时 |
| S2 | 显式上下文转换：`transformContext()` → `convertToLlm()` | `context.prepare()` → `transcript.as_messages()` | 我们的 messages 本来就是 LLM 格式，所以第二步是恒等映射 |
| S3 | turn = 一次 LLM 调用 + 工具执行 | round | 同构概念，可互相映射 |
| S4 | `beforeToolCall` / `afterToolCall` 拦截点 | `PreToolUse` / `PostToolUse` | 位置相同（参数校验后、结果落地前） |
| S5 | 事件订阅把 UI 与循环解耦 | 事件注册表 + `trigger_hooks` | 目的相同；实现不同（见 D4） |

### 8.2 不同，且**不跟**（8 条，逐条给理由）

| # | pi | 我们 | 为什么不跟 |
|---|---|---|---|
| D1 | `AgentMessage` 可声明合并自定义角色，`convertToLlm` 过滤 UI-only 消息 | messages 只有 LLM 三种角色 | **属于新功能**：引入自定义消息类型会改变全部工具与压缩的输入契约。判据 §2：这是"预测中的问题"，不是已发生的问题 |
| D2 | 工具失败要求**抛异常**，循环捕获为 `isError: true` | 工具失败**返回文本**（`错误：…`） | **改变 8 个工具与全部相关测试的行为**，超出"只重组"的范围。我们的约定有独立价值：失败原因直接进上下文，模型无需额外解析 |
| D3 | 工具默认**并行**执行，`executionMode` 可覆盖，批次内任一 sequential 则整批串行 | 串行；只有 `subagent` 内部并行 | **属于新功能**。但接口要留得住：`execute_batch` 的签名允许将来换并发实现而不改调用方 |
| D4 | 异步事件流 + `await` 订阅者顺序结算 | 同步、进程内、可变 dict | 我们没有 UI 进程内订阅之外的消费者；异步化会引入"回调何时结算"的新失败面（判据 §7 对异步化的追问） |
| D5 | steering / follow-up 队列（运行中插入转向、排队后续） | 无 | **属于新功能**。需要它时最可能的落点是 `hooks.py` 新增一个 `Steering` 事件，而不是改 `Transcript` |
| D6 | `session/` + `repo` / `storage` 三层，JSONL 与 SQLite 后端，带 conformance 测试套件 | 无持久化 | **属于新功能**。阶段 12 已落地其中的条目树 / 值 / 分支 / 变更线 / JSONL 与内存两个后端，并沿用同一套 conformance 用例；`fork`、usage 台账、operation 状态机仍未做（无真实使用者） |
| D7 | `harness/runtime/drive/` 的 boundary / checkpoint / recovery / reconcile | 无 | **属于新功能**（崩溃恢复）。我们没有长时任务与进程重启需求 |
| D8 | `harness/compaction/` 以摘要为主（含 branch-summarization） | 五步阶梯，**以"落盘留恢复路径"为主**，摘要只在整理之后 | 思路相近、取值不同。我们的更保守：优先保留可恢复信息，代价是磁盘占用；pi 的更节省上下文，代价是信息有损。这是 §10 里"边界代价"的一个实例 |

### 8.3 差异归因

D1–D8 里，D3/D5/D6/D7 是**能力差异**（我们没做），D1/D2/D4 是**取值差异**（做了但选得不同），D8 是**同一问题的不同取舍**。只有 S1/S2 是**结构差异**（同样是重组，pi 选得更彻底）——这才是本文档要借鉴的部分；其余不借鉴的理由与能力清单一致。

## 9. 集成点与改动范围

分两阶段，理由见 §10（可逆性等级不同）。

### 阶段 A —— 原地抽取（不移动文件）　✅ 已实施

> 下表里的模块路径是**阶段 B 之前**的名字；阶段 B 之后按 §15 的映射改名。

| 文件 | 动作 | 影响面 |
|---|---|---|
| `src/avid/transcript.py` | 新增 `Transcript` | 无（新文件） |
| `src/avid/state.py` | 新增 `RunState` | 无 |
| `src/avid/context.py` | 新增 `prepare()`；`compact.py` 保留五步实现与阈值 | 无 |
| `src/avid/execution.py` | 新增 `execute_batch()`；从 `agent.py` 移出 `_execute_one` | `agent.py` 少约 70 行 |
| `src/avid/agent.py` | 改为只做调度；`agent_loop` 公开签名不变 | 60+ 处调用点**不改** |
| `src/avid/tools/todo.py`、`skill_loader.py`、`permission.py` | 删除 `bind*` / `current*`（contextvars 退役） | 各少 10–15 行 |
| `src/avid/tools/subagent.py` | 自建 `RunState` | 约 10 行 |
| `tests/test_agent.py` | 两个 fixture（`no_hooks`、`point_skills_at`）改为注入 `RunState` | **测试改动集中在这里** |
| 其它测试 | 仅 import 路径受影响（阶段 A 内几乎为零） | 极小 |

### 阶段 B —— 分包（移动文件）　✅ 已实施（映射与偏差见 §15）

| 动作 | 影响面 |
|---|---|
| `llm.py` / `config.py` → `ai/` | import 路径全改 |
| `agent.py` → `runtime/loop.py`；`context.py` / `execution.py` / `state.py` / `hooks.py` → `runtime/` | 同上；`hooks.py` 不改名 |
| `transcript.py` → **`ai/`**（原计划放 runtime，落地时发现会让 policy 反向依赖 runtime） | 同上 |
| `compact.py` / `permission.py` / `tools/todo.py` / `skill_loader.py` → `policy/` | 同上 |
| 全部测试的 import | 机械替换 |
| `AGENTS.md` §1 当前状态、`docs/design/*` 路径引用 | 文档同步 |

**阶段 B 不做的事**：不合并文件、不改签名、不调整职责——纯搬家。这样出问题时回滚范围明确。

## 10. 边界代价、方案对比与决策

### 10.1 三个方案

| 维度 | 方案一 全量分包 | 方案二 原地抽取 | 方案三 不动 |
|---|---|---|---|
| 逻辑隔离（压缩/工具/状态不再与循环纠缠） | 完整 | **完整** | 无 |
| 目录即边界的可见性 | 有 | 无 | 无 |
| 改动文件数 | 20+ | 9 | 0 |
| 测试 import 改动 | 全部 | 一处 fixture | 0 |
| 与既有 `.gitignore` 白名单/文档路径的同步成本 | 有 | 无 | 无 |
| 回滚难度 | 中等（多文件路径） | **易**（逐文件还原） | — |
| 收益 | B 的全部 + 目录可见性 | 逻辑分层 | 无 |

### 10.2 决策：先原地抽取，再分包（§9 的阶段 A → 阶段 B）　**两步均已实施**

- **解决了什么**：循环不再认识策略值（阈值、文案、规则）与压缩编排；messages 有了唯一所有者；三个 contextvars 与四个局部标志变成显式状态。
- **牺牲了什么**：多一层间接（读代码要跳 5 个文件）；`ToolImpl` 从 `Callable[[dict], Any]` 放宽为 `Callable[..., Any]`（为让两个有状态工具拿到 `RunState`）。
- **增加了什么复杂度**：`Transcript` 的 4 个写入方法；`prepare` 多一个 `summarize` 参数；`RunState` 多一个类。
- **在什么条件下成立**：单进程、单线程运行一个 `agent_loop`；工具全部同步；压缩步骤只依赖 `messages` 与阈值。这三条现在都成立。
- **什么条件下失效**：需要并行执行同一 `Transcript`（并发写）、需要跨进程恢复（`RunState` 不可序列化）、需要按轮次动态改变压缩步骤顺序（`prepare` 的固定顺序会不够）。
- **出现什么信号时重新考虑**：① `prepare` 里出现第一个"按轮次分支"；② 有工具需要在 `execute_batch` 之外访问 `Transcript`；③ 会话持久化立项（那时 `Transcript` 要加版本与迁移，`RunState` 要拆分持久/易失部分）。

**为什么先原地抽取、后分包**（判据 §11 可逆性）：原地抽取是**易撤销**（逐文件 git checkout 即可），分包是**中等**（20+ import 路径 + 文档同步）。按"按撤销难度分配论证成本"，先用最小可逆的一步拿到全部逻辑收益并在真实运行中验证，再决定是否付搬家的代价。

### 10.3 反事实测试（判据 §10）

| 假设 | 哪些边界仍然成立 | 哪些会崩 |
|---|---|---|
| 需求全变（换场景） | `Transcript` / `execution` / `ai` 全部成立 | `policy/` 四个模块全部要换——这正是把它们放一起的理由 |
| 规模 ×100（消息量、工具数） | 分层成立 | `Transcript.estimate_chars` 的 O(n) 全量扫描成为热点，需要缓存；`policy/compaction` 的落盘策略要改 |
| 模型 API 长期不可靠 | `ai/` 的边界成立 | `loop` 的重试策略要升级；`RunState.retried` 的"最多一次"要重新评估 |
| 数据存储更换 | `Transcript` 作为唯一序列化点成立 | 无（当前无存储） |
| 团队从 1 变 10 | 层边界即 ownership 边界 | `policy/` 单目录会成为冲突热点，需按策略再分人 |

## 11. 12 组判据的覆盖情况

| # | 检查点 | 相关 | 结论落在 | 未验证假设 |
|---|---|---|---|---|
| 1 | 变化优先 | ✓ | §1 | — |
| 2 | 具体先行 | ✓ | §2（含删除测试） | — |
| 3 | 耦合 | ✓ | §1.1（逐条耦合机制） | — |
| 4 | 边界与决定权 | ✓ | §3、§4.2 | 分包后的 ownership 分配（单人项目暂无意义） |
| 5 | 数据所有权与状态生命周期 | ✓ | §4.1、§4.2、§5.2 | ~~`Transcript` 与 `RunState` 将来如何被会话持久化拆分~~ → **阶段 12 已回答**：`Transcript` 仍是内存里 messages 的唯一所有者，会话独占持久化条目，两者经 `agent_loop(on_message=…)` 观察点相连（见 §16） |
| 6 | 不变量 | ✓ | §6（I1–I6） | — |
| 7 | 失败与恢复 | ✓ | §7 | — |
| 8 | 并发与一致性 | **部分** | §5.3、§16 | 唯一并发是 `subagent` 线程池；同一 `Transcript` 的并发写**当前不存在**。阶段 12 给会话加了 `MutationLine`（单写者 + seal/drain），跨线程语义有测试覆盖，但**同一会话被多进程同时打开没有互斥**（无文件锁） |
| 9 | 依赖与不可靠边界 | ✓ | §7、§8、§16 | ~~pi 实现的内部细节（只读了 README 与文件树，未读源码）~~ → **阶段 12 已读 `harness/session` 全部源码**，差异逐条落在 §16 的对照表里；pi 的 `harness/runtime/drive/` 仍是未验证假设 |
| 10 | 边界代价与权衡 | ✓ | §10 | 规模 ×100 后的性能结论是估算，非实测 |
| 11 | 可逆性与决策强度 | ✓ | §10.2（分两步） | — |
| 12 | 运行与演进 | ✓ | §10.2 失效信号、§13 | 性能数字、部署形态（当前是单机 CLI） |

## 12. 可验证的验收标准

每条都给出可执行的判定命令或断言，不接受"结构更清晰"这类无法证伪的说法。

| # | 验收标准 | 判定方式 |
|---|---|---|
| 1 | **行为不变**：`agent_loop` 公开签名逐个参数不变 | `inspect.signature(agent_loop)` 与重构前的快照比对 |
| 2 | **行为不变**：现有 295 项测试全部通过，且**不改任何调用点** | `uv run pytest -q` → 295 passed；`git diff` 中 `tests/` 只有 fixture 段改动 |
| 3 | **循环不再认识策略值**：`loop.py` 不出现阈值常量与策略名 | `grep -E "TOOL_RESULT_CHAR_BUDGET\|MAX_MESSAGES\|CONTEXT_CHAR_LIMIT\|APPROVAL_RULES\|SUMMARY_SYSTEM" src/avid/runtime/loop.py` → 无匹配 |
| 4 | **压缩顺序只在一处** | `grep -rn "tool_result_budget\|snip_compact\|micro_compact" src/avid --include=*.py` → 除 `policy/compaction.py` 与 `runtime/context.py` 外无匹配 |
| 5 | **messages 只有一个所有者** | `grep -rn "messages\.append\|messages\.extend\|messages\[:\]" src/avid --include=*.py` → 只出现在 `ai/transcript.py` |
| 6 | **结构不变量由所有者保证**：非法的 `replace_all` / `splice` 抛错且不改状态 | 新增测试：构造会产生孤立 tool 结果的候选 → 断言抛 `TranscriptError` 且 `transcript.validate() == []` |
| 7 | **一次性标志各只有一个写入点** | `grep -rn "compacted = True" src/avid` → 1 处；`grep -rn "retried = True" src/avid` → 1 处 |
| 8 | **①②③ 在类型上无法调用模型** | `inspect.signature(prepare)` 有 `summarize`，且 `policy/compaction.py` 的 `tool_result_budget` / `snip_compact` / `micro_compact` 三个函数签名中无 `chat` |
| 9 | **依赖方向单向**：`runtime/` 不 import `policy/` 的具体实现 | `grep -rn "^from \.\.policy" src/avid/runtime/*.py`（**只匹配顶层 import**）→ 只出现在 `context.py`（调 compaction）、`state.py`（持有 todo / skills）、`hooks.py`（注册默认回调）。**两处措辞修正**：① 初稿漏了 `hooks.py`——它是扩展点的注册处，默认回调必须有人注册；把注册搬去别处只换 import 位置、不换隔离效果。② `loop.py` / `state.py` 里 `AskUser` 这类**只用于注解**的类型别名走 `if TYPE_CHECKING:` 导入（缩进的那一行），不算运行时依赖——注解是惰性的，跨层只为标注而 import 会让断言从"可 grep 的事实"退化成"文档说没有、代码里有"。`loop.py` 与 `execution.py` 对 policy **零运行时依赖** |
| 10 | **压缩日志与计数不减少** | 现有 `test_compaction_is_logged` / `test_compaction_count_reaches_the_stop_hook` 通过 |
| 11 | **端到端不变**：同一脚本化对话在重构前后产出相同 messages 序列 | 录制-回放测试：固定 `chat` 返回值序列，断言最终 `transcript.as_messages()` 与快照一致 |
| 12 | 测试总数不减 | `uv run pytest -q` 的 passed 数 ≥ 295 |

## 13. 适用条件与失效信号

- **成立条件**：单进程 CLI、单线程运行、工具同步、压缩步骤顺序固定、无持久化需求。
- **不成立条件**：需要跨进程恢复；需要并发写同一 `Transcript`；需要按轮次动态改变压缩策略组合；需要两个进程同时写同一个会话文件。
- **下一次变化最可能落在哪**：按可能性排序——① 新增策略值（阈值/文案），落在 `policy/`，成本最低；② 会话持久化的**下一段**（压缩条目、usage 台账、fork），落在 `session/`，需要格式版本迁移设计；③ 并行工具执行，落在 `execution.execute_batch` 的实现，签名不变；④ 换 provider 或加 provider，落在 `ai/`。
- **重新评估的信号**（与 §10.2 一致）：`prepare` 出现按轮次的分支；工具需要直接访问 `Transcript`；会话持久化立项（**已触发**，见 §16）；续接的长会话每次都要重新压缩（触发压缩条目）；会话列表的读取代价变得可感（触发把会话名冗余进 header）。

## 14. 落地记录（阶段 A）

阶段 A 已实施，一个提交：`refactor(runtime): 解耦 agent loop，抽出消息所有者、运行状态、上下文编排与工具执行`。

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | 公开签名不变 | `inspect.signature` 逐参数比对通过（11 个参数、keyword-only 一致） |
| 2 | 调用点不改即全过 | **326 passed**（原 295），`agent_loop` 调用点零改动 |
| 3 | 循环不认识策略值 | `grep` 阈值常量与 `APPROVAL_RULES` / `SUMMARY_SYSTEM` / `DENIED_CONTENT` → `agent.py` 无匹配 |
| 4 | 压缩顺序只在一处 | 五个步骤名只出现在 `compact.py` 与 `context.py` |
| 5 | messages 只有一个所有者 | `messages.append` / `extend` / `[:]` 只在 `transcript.py` |
| 6 | 非法结构改动被拒且不改现状 | `test_replace_all_rejects_and_leaves_state_untouched`、`test_splice_rejects_a_cut_that_orphans_a_result` |
| 7 | 一次性标志各一个赋值点 | `compacted = True` 在 `context.py`、`retried = True` 在 `agent.py`，各 1 处 |
| 8 | ①②③ 类型上碰不到模型 | 三个函数签名无 `chat`（原有测试继续通过） |
| 9 | 依赖方向单向 | **阶段 B 分包后才可验证** |
| 10 | 日志与计数不减 | 原有相关测试全部通过 |
| 11 | 行为不变 | 同一脚本化对话在 `HEAD~1` 与 `HEAD` 上产出的 messages 序列与返回值**逐字节一致**（`dev/tmp/golden_scenario.py`） |
| 12 | 测试数不减 | 295 → **326** |

**与设计的偏差**（落地时才发现，已回写上文对应小节）：

1. `STATEFUL_TOOLS` 是 **3 个**不是 2 个——补上 `subagent`（它要读 `RunState.auto_approve`）。（阶段 18 起扩到全部 14 个，见 §4.2。）
2. 写入方法是 **5 个**不是 4 个——`append_many` 与 `set_content` 分开。
3. **删掉了 `permission.RUN_AUTO_APPROVE` / `bind_auto_approve`**：`auto_approve` 由 `execution` 从 `RunState` 放进 PreToolUse 事件 context，跨线程传播结构上自然成立，不再需要运行级 ContextVar 做中转。
4. 原 `test_snip_gives_up_when_no_safe_cut_exists` 用的是**非法** messages（孤立的 tool 结果），`Transcript` 现在会直接拒绝——改用合法的"头尾保留量之和超过消息数"触发同一条分支。
5. 编排测试从 `test_agent.py` 移到新的 `tests/test_context.py`：编排住在 `context.prepare`，在这一层测不需要伪造整个循环。

**回滚方式**：`git revert <commit>`，或逐文件 `git checkout HEAD~1 -- <file>`。阶段 A 不动文件位置、不改公开签名，因此回滚不存在中间态。

**阶段 B 未做**：文件分包（`ai/` / `runtime/` / `policy/`）尚未进行——按 §10.2 的分步决策，等阶段 A 在真实使用中稳定后再评估。

## 15. 落地记录（阶段 B）

阶段 B 已实施：文件搬进 `ai/` / `runtime/` / `policy/` 三个包，只改 import 路径与 fixture 引用。

**迁移映射**

| 原路径 | 新路径 |
|---|---|
| `llm.py` | `ai/client.py` |
| `config.py` | `ai/config.py` |
| `transcript.py` | **`ai/transcript.py`**（不按原计划放 runtime） |
| `agent.py` | `runtime/loop.py` |
| `context.py` / `execution.py` / `state.py` / `hooks.py` | `runtime/` 同名 |
| `compact.py` | `policy/compaction.py` |
| `permission.py` | `policy/permission.py` |
| `skill_loader.py` | `policy/skills.py` |
| `tools/todo.py` | `policy/todo.py` |
| `cli.py`、`tools/`（其余）、`skills/` | 不动 |

**三处不是纯搬家**（落地时才发现，已回写 §3 / §9 / §12）：

1. **`transcript.py` 归 `ai/` 而不是 `runtime/`**。`policy/compaction.py` 的五步都要操作 `Transcript`；放 runtime 会让 policy 反向依赖 runtime，违反判据 §4 的依赖方向。而工具调用与结果的配对本就是**协议要求**（端点会拒绝不配对的请求），归协议层名实相符。
2. **`loop.py` 甩掉最后一个 policy 依赖**。它原本要 import `policy.todo` 取提醒阈值与文案；改为 `RunState.todo_reminder(threshold)` 决定"该不该提醒、提醒什么"，`RunState.for_run()` 负责建注册表，`RunState.system_prompt()` 负责拼提示——**行为不变，但循环从此零 policy 依赖**。
3. **判据 9 的措辞修正**：`runtime/hooks.py` 必须 import `policy.permission` 才能注册默认回调。原判据漏了这条必然后果；搬代码换不来隔离，所以改的是判据。

**验收结果**

| # | 标准 | 结果 |
|---|---|---|
| 1 | 公开签名不变 | `inspect.signature` 逐参数一致（11 个参数） |
| 2 | 测试不改调用点全过 | **326 passed**（与阶段 A 同数） |
| 3 | 所有模块可导入 | `pkgutil.walk_packages` 逐个 import，24 个模块无异常 |
| 4 | 无旧路径残留 | `grep "avid.agent\|avid.compact\|avid.llm\|avid.skill_loader\|avid.tools.todo\|avid.transcript\|from avid import"` → 测试与源码无匹配 |
| 5 | messages 只有一个所有者 | 仍只在 `ai/transcript.py` |
| 6 | 一次性标志各一个赋值点 | `compacted` 在 `runtime/context.py`、`retried` 在 `runtime/loop.py` |
| 7 | 判据 9 | `runtime/` 的 policy import 只在 `context.py` / `state.py` / `hooks.py`；`loop.py` 与 `execution.py` 零依赖（阶段 15 一度给 `loop.py` 加了 `AskUser` 的运行时 import，已改回 `TYPE_CHECKING`，见 §12 判据 9 的第 ② 条修正） |
| 8 | 行为不变 | black-box 脚本在分包前后产出的 messages 序列与返回值**逐字节一致** |
| 9 | 真实运行 | 通过；logger 名已随模块路径更新（`avid.runtime.loop` / `avid.policy.skills`） |

**回滚方式**：`git revert <commit>` 即可——分包不改签名、不改职责，回滚只影响 import 路径。

## 16. 落地记录（阶段 12，会话持久化）

需求来自"运行时要有可续接的会话"这一已明确的必经项（`dev/plan/roadmap.md` 的"尚未做"清单），
参考实现是 `earendil-works/pi` 的 `packages/agent/src/harness/session/`（18 个文件、约 4400 行 TS）。
本阶段读了它的全部源码，而不是只看 README——§11 里那条"pi 实现细节是未验证假设"因此作废。

### 16.1 参考实现的机制（读完源码后的结论）

| 层 | 文件 | 职责 |
|---|---|---|
| 契约 | `types.ts` / `values.ts` | `Entry` 树、`Write` 事务、`Storage` / `Session` / `SessionRepo` / `Branch` 接口、地址化 KV |
| 会话 | `session.ts` / `mutation-line.ts` / `commit.ts` | 分支、变更线、值、关闭；seq / timestamp 分配与提交前校验 |
| 状态 | `in-memory-storage-state.ts` | 两个后端共用的物化状态（entries / values / lists / usage / next_seq） |
| 后端 | `memory.ts` / `jsonl/*` | 进程内与文件（header + 每提交一行，open 时重放，撕裂行修复） |
| 语义 | `testing/conformance/*` | **一套用例参数化到所有后端**，行为一致靠测试而不是文档 |

抽取出的关键行为（本阶段按此对齐）：create 不隐式建分支；分支头是值 `branch.tip/<name>`、
条目靠 `parentId` 串链；一次提交分配连续 seq 与同一 timestamp，落地前校验重复 id / 缺 parent /
seq 非单调；`SessionMutation.commit` 恰好一次、`end` 后失效；`close` 先 seal 变更线再 drain
已授予的作业；仓库层拒绝重复 id、拒绝 open 已打开的会话、拒绝 delete 打开中的会话；JSONL
单写裸对象 / 多写数组、末尾撕裂行忽略并原子重写、无索引（靠目录扫描查重）。

### 16.2 跟 / 改 / 不做

**跟**：上列全部行为，以及"一套一致性用例跑所有后端"的做法。

**改**（每条都是 Avid 现状逼出来的）：

| # | pi | Avid | 理由 |
|---|---|---|---|
| A1 | 全 async，每次调用传宿主 `Context`（fs / env / telemetry） | 同步 API；能力在构造期注入（`root` / `now` / `id_generator`） | Avid 内核同步；`Context` 是 pi 的异步宿主抽象，删掉不丢边界——文件仍被 `JsonlSessionRepo` 独占 |
| A2 | 会话存全局根 + `--cwd--` 目录名编码 | 存工作区内 `.avid/sessions/` | 工作区边界已由 `tools/workspace.py` 定义；阶段 8 的教训是落盘必须在工作区内才能被工具读回 |
| A3 | `SessionStats{messageCount, usage}` | `SessionStats{message_count}` | usage 现在只活在 `Turn` 里，没有真实读取者 |
| A4 | 标量值 + 列表 + `scanValues(prefix)` | 只有标量 `get/set/delete_value` | 列表与 prefix 扫描的使用者只有 fork 与待定帧，两者都没做 |
| A5 | `mutate` 内嵌套写会排队（JS 表现为死锁风险） | 同线程嵌套变更直接抛 `SessionBusyError` | 同步阻塞语义下排队必自锁；改成更早、更清楚的失败 |
| A6 | 13 态 operation 状态机 + checkpoint / recovery | 无；投影时把没有结果的 tool_calls 批次丢掉 | 可恢复的在途操作属于新功能；"崩在批次中间"这个真实症状用更小的办法解决 |
| A7 | 压缩写 `compaction` 条目 | 只写 `message` 条目 | `CompactReport` 不带摘要正文与保留尾巴；触发条件：续接的长会话每次都要重新压缩 |
| A8 | `fork` / `branch_summary` | 不做 | fork 语义绑定 lane config 与 operation 台账，Avid 无此概念 |
| A9 | uuidv7 | 自实现 uuidv7 形状（约 15 行，标准库） | 不新增依赖，同时保留"id 时间有序"这一真实收益 |

**另外两处主动对齐**：pi 的 `list()` 在内存后端是插入序、JSONL 后端是 createdAt 倒序，Avid
统一为"createdAt 倒序、同刻按 id 升序"；pi 的 JSONL 只在重写时写 `nextSeq`，Avid 保留同一
高水位字段并在重放后取两者较大值。

### 16.3 模块与集成

新增 `src/avid/session/`（11 个模块 + `__init__.py`）：`errors` / `types` / `values` / `state` / `ids` /
`mutation` / `session` / `memory` / `jsonl` / `projection` / `recorder`。公开面只有
`src/avid/session/__init__.py` 里 `__all__` 列出的名字。

集成只有一处：`cli.py` 建 `JsonlSessionRepo(工作区/.avid/sessions)`，用
`projection.messages_for_branch()` 取历史，把 `recorder.on_message` 交给
`agent_loop(on_message=…)`。于是

* `loop.py` 只多一个回调参数，**不 import 会话层**；`_submit_input` 从返回 `bool` 改为返回
  触发消息下标，因为注入会改写那条消息，会话要存注入后的版本；
* `session/` **不 import** `runtime` / `policy` / `tools` / `ai`（连 `Transcript` 都不 import：
  投影自己修不完整批次，续接时由调用方交给 `Transcript` 再次校验）。

### 16.4 落地时发现的偏差（已回写上文与模块 docstring）

1. **cursor 必须是排他的**。初稿写成包含语义，一致性用例在 `memory` 与 `jsonl` 上同时变红：
   翻页会重复上一页最后一条。两个后端同一条用例同时失败，正是"一套用例跑所有后端"的价值。
2. **`create(id="")` 会被 `id or generator.next()` 悄悄换成新 id**（两个后端都错）。改成显式判断
   `is None`；非法 id 归 `SessionInvalidIdError`，两个后端共用同一条校验。
3. **投影的初版语义（"截断尾巴"）不够**：崩溃后**续接**出来的轮次会让半截批次落在链中间，只截
   尾巴会把有效轮次一起丢掉。改成"只丢不完整批次与孤儿 tool 结果，其余保持原序"，函数名同步改为
   `repair_incomplete_batches`。
4. **JSONL 重放必须带行号**：把 `SessionStorageError` 原样抛出会丢掉"第几行"，坏行定位不到。
5. **`storageVersion` 与格式版本 `v` 是两条错误路径**：`v` 属于 header 解析（格式不认），
   `storageVersion` 属于仓库打开（存储代际不认），分别报错而不是合成一条。
6. **`close_storage` 开关是必需的**：pi 用 `MemorySessionFacade` 让"close 之后仍能 reopen"
   （facade 不关底层 storage）；Avid 用显式开关（内存后端 `False`，JSONL 后端默认 `True`），
   否则内存后端 close 之后无法重启续接。
7. **`SessionState.validate` 同时服务运行期提交与文件重放**：两个路径共用同一函数，所以"运行时
   接受的"和"重放时接受的"不可能分叉——这是把 JSONL 版本检查放到仓库层之后剩下的唯一校验点。

### 16.5 验收结果

| # | 标准 | 结果 |
|---|---|---|
| 1 | 全量测试 | **437 passed**（阶段 11 为 326，本阶段 +111） |
| 2 | 一套一致性用例跑两个后端 | `tests/session_cases.py` 17 条 × memory/jsonl = **34 passed**，用例只碰公开 API |
| 3 | 生命周期四条 | create 不隐式建分支 / 重复 id 拒绝 / open 已打开拒绝 / delete 打开中拒绝且删除后 open 与 delete 均失败（`lifecycle-*`、`ownership-*`） |
| 4 | 销毁与关闭 | close 后读写全拒；跨线程 close 会等作业结束且提交不丢；排队中的变更在 seal 后拿到 `SessionClosedError`；同线程 `mutate` 内 close/begine 报 `SessionBusyError`（`test_session_state.py`） |
| 5 | 变更恰好一次 | 二次 commit 报错、end 后能力作废、零次 commit 合法 |
| 6 | 提交校验与失败原子性 | 重复 id / 缺 parent 被拒，状态与统计不变，且失败的提交**不消耗 seq** |
| 7 | 分支与查询 | 串链、tip 前进、oldestFirst/limit/cursor/type、desc/asc 翻页到末尾返回空 |
| 8 | 文件格式 | header + 每提交一行（多写为数组）、重启后状态一致、撕裂行忽略并修复、坏行报行号、非单调 seq / 缺 parent / 未知格式版本 / 未知存储版本各有断言、目录扫描查重、`.tmp` 不残留 |
| 9 | 投影 | 完整链原样、不完整批次与孤儿结果被丢、结果通过 `Transcript.validate()` |
| 10 | 循环集成 | 每条结算消息按序落库；工具往返落库；第二轮输入含历史；注入后的触发消息落库；Stop nudge 落库；写入失败在调模型前中止 |
| 11 | CLI | 新建/续接/命名/列举/销毁+参数错误退出码（`tests/test_cli_session.py` 11 项） |
| 12 | 依赖方向 | `grep -rn "session" src/avid/runtime src/avid/policy src/avid/tools` 无 import；`grep -rn "from \.\.\(runtime\|policy\|tools\|ai\)" src/avid/session` 无匹配 |
| 13 | 公开签名 | `agent_loop` 新增一个带默认值的 `on_message`，其余 11 个参数逐参数不变 |

### 16.6 回滚方式与未做

**回滚**：`git revert` 三个提交即可（会话包、循环观察点、CLI 旗标）。没有数据迁移问题——`.avid/sessions/`
是新增目录，旧版本程序看不见它；反过来，本阶段写的会话文件在回滚后成为无用文件，不会被读取。

**未做**（全部写明触发条件）：压缩条目（长会话续接需要重复压缩时）、usage 台账（要统计 token
成本时）、fork（要从某条历史分叉继续时）、operation 状态机（要在途任务跨进程恢复时）、
文件锁（两个进程可能同时写同一会话文件时）、SQLite 后端（会话数量让 JSONL 重放变慢时）。

## 17. 下一阶段设计：任务图（Task DAG）——补 TodoWrite 的依赖与分工缺口

本章是**下一阶段的设计稿**（尚未实现），用来补齐 `todo_write` 在两类事情上的空缺：**任务之间的依赖关系**与**谁在做哪一条**。文中的代码块分两种来源，逐块标注：

* 【原文】——来自本阶段的原始设计稿，**逐字保留**（签名、消息模板、SVG 源码都不改）；
* 【补出】——原文提到但未给出实现的接口，按原文语义补出，供实现与测试对齐。

术语统一为 `pending` / `in_progress` / `completed`（`claim` / `complete` 是**动作**，不是状态）。

### 17.1 缺口：清单项只有内容与状态

现状（`policy/todo.py`）：`TodoList.items` 是 `{"content": str, "status": str}` 的数组，
`replace()` 整份替换并原子校验，最多一项 `in_progress`；状态住在 `RunState.todo`，
也就是**一次运行之内**，运行结束即消失。它解决了"把多步任务显式计划出来"，但回答不了
下面三个问题：

| 缺口 | 具体表现 | 现有机制的哪一步失效 |
|---|---|---|
| **依赖** | `pending` 只表示"还没开始"，**不区分"现在可以开工"与"被上游挡住"**。Harness 没有任何字段能回答"这条能不能开始" | 顺序只活在 messages 里。阶段 8 的压缩会改写 messages，阶段 12 的续接会把历史换成另一条链——判断依据随之丢失 |
| **分工** | 没有 `owner`：阶段 6 的 `subagent` 并行派发之后，哪条已被认领、`in_progress` 是谁在执行，都没有记录 | `TodoList` 没有身份字段，主 agent 与子 agent 的进度无法对账 |
| **标识与恢复** | 清单项靠数组下标与文案定位，**跨会话引用不到同一条任务**；长描述无处安放（`content` 只有一句话） | `todo_write` 是"整份替换"，既没有稳定 id，也没有"按 id 取详情"的入口 |

任务图补的正是这三处：`blockedBy`（依赖）、`owner`（分工）、`task_xxxxxxxx` + `.tasks/{id}.json`（稳定标识与跨会话恢复）。

**与 `todo_write` 的关系：并存，不替换。** 按 `architecture-criteria.md` 的删除测试：
删掉 `TaskStore` 则"跨会话的依赖判断"没有别处可放；删掉 `todo_write` 则"一次运行内的即时进度"
失去最轻量的表达（整份替换、零落盘）。两者服务的时间尺度不同——任务是跨会话的图，TODO 是本轮运行的清单。

### 17.2 Task 数据结构

【原文】

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str          # pending | in_progress | completed
    owner: str | None    # 负责当前任务的 Agent
    blockedBy: list[str] # 依赖的任务 ID 列表
```

每个任务是一个 JSON 文件，存于 `.tasks/` 目录：

```
.tasks/
├── task_9f3c1a7e.json    # {"id": "task_9f3c1a7e", "subject": "schema", ...}
├── task_1b2c3d4e.json
└── task_5a6b7c8d.json
```

ID 使用 `task_` 加 8 位随机十六进制字符生成。创建文件时使用排他写入；如果 ID 已存在，就重新生成。

【补出】上面两句话的实现形状（原文只给了语义，没给代码）：

```python
import json
import re
import secrets
from dataclasses import asdict
from pathlib import Path

TASKS_DIR = Path(".tasks")
ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")


def new_task_id() -> str:
    """task_ + 8 位随机十六进制。"""
    return f"task_{secrets.token_hex(4)}"


def write_task_exclusively(path: Path, task: Task) -> bool:
    """排他写入：文件已存在就返回 False（调用方据此重新生成 ID）。"""
    try:
        with path.open("x", encoding="utf-8") as handle:   # "x" = O_EXCL
            handle.write(json.dumps(asdict(task), ensure_ascii=False, indent=2))
    except FileExistsError:
        return False
    return True
```

* **解决了什么**：一个任务一个文件，文件名即 ID，排他写入使"两个创建者撞同一个 ID"必然有一方失败而不是互相覆盖。
* **牺牲了什么**：没有索引文件，列表要扫目录；任务数量上千时 `list_tasks()` 会变慢（触发条件：单目录任务数成为可感延迟时，再加一层按状态的索引）。
* **在什么条件下成立**：单机、单工作区、任务量在几十到几百；跨工作区共享任务不在本章范围。

### 17.3 TaskStore

TaskStore 负责校验任务 ID 和读写 JSON 文件，`TASKS = TaskStore(TASKS_DIR)` 是本章使用的任务存储。

【补出】按原文语义补出的接口（`create` / `update_dependencies` / `save` 都由原文的调用点确定）：

```python
class TaskStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def create(self, subject: str, description: str = "") -> Task:
        """校验 subject，分配随机 ID，排他写入 .tasks/{id}.json。

        新任务的 blockedBy 固定为空、status 为 pending、owner 为 None；
        ID 已存在（文件在）就重新生成，不覆盖已有任务。
        """

    def update_dependencies(self, task_id: str, add_blocked_by: list[str]) -> Task:
        """先校验整次修改，再统一保存（见 17.4.2）。"""

    def save(self, task: Task) -> None:
        """覆盖写单个任务文件（只用于已存在任务的字段更新）。"""

    def load(self, task_id: str) -> Task | None:
        """ID 非法或文件不存在都返回 None——调用方据此给出可读的错误文本。"""


TASKS = TaskStore(TASKS_DIR)


def load_task(task_id: str) -> Task | None:
    return TASKS.load(task_id)


def list_tasks() -> list[Task]:
    """列出全部任务（按 id 排序）。"""


def incomplete_dependencies(task: Task | None) -> list[str]:
    """返回还没 completed 的前置任务 ID。

    只要有一个不是 completed，**或者对应文件已经不存在**，就算未完成；
    task 本身为 None（任务文件缺失）同样视为未完成。
    """
```

**校验点**（TaskStore 的唯一职责边界）：ID 必须匹配 `^task_[0-9a-f]{8}$` 且解析后仍落在
`.tasks/` 之内（拒绝 `../`、绝对路径与 NUL）——这是阶段 2 给文件类工具立的同一条规矩，
放到这里是为了不让"任务 ID"成为绕过工作区边界的第二个入口。

### 17.4 工具语义

六个工具。返回值一律是**文本**（与项目约定一致：工具失败返回文本、不抛异常，见 §8.2 D2），
`create_task` / `update_task` 例外——它们的函数返回 `Task`，工具层把 `task.id` 渲染成结果文本。

**【原文】+ 实例化示例**：下面的签名与消息模板逐字保留；"返回"一列是按模板代入一组示例 ID 的结果。

#### 17.4.1 `create_task`：创建任务

【原文】

```python
def create_task(subject: str, description: str = "") -> Task:
    return TASKS.create(subject, description)
```

TaskStore.create 检查 subject，分配随机 ID，再把任务写入 `.tasks/{id}.json`。新任务的 blockedBy 固定为空，工具结果会把运行时生成的 ID 返回给模型。

| 输入 | 返回 |
|---|---|
| `create_task("schema", "设计表结构")` | `task_9f3c1a7e`（运行时生成的 ID，工具结果就是它） |
| `create_task("")` | `错误：subject 不能为空`（【补出】原文只规定"检查 subject"，未给消息文案；不落盘） |

#### 17.4.2 `update_task`：使用返回的 ID 添加依赖

【原文】

```python
def update_task(task_id: str, addBlockedBy: list[str]) -> Task:
    return TASKS.update_dependencies(task_id, addBlockedBy)
```

任务图采用两阶段构建：先创建所有节点，再使用 create_task 返回的 ID 调用 update_task 添加边。模型可能在一条回复里同时发出多个工具调用，而这些同级调用在任何工具结果产生前就已经确定，因此某个 create_task 无法直接使用另一个调用刚生成的 ID。

update_task 会先校验整次修改，再统一保存。目标任务和依赖必须存在，目标必须仍为 pending 且无人认领，并且不能形成自依赖或环。重复添加已有依赖是安全的，不会产生重复边。

| 输入 | 返回 |
|---|---|
| `update_task("task_5a6b7c8d", ["task_9f3c1a7e", "task_1b2c3d4e"])` | 更新后的 `Task`（工具结果回 `task_5a6b7c8d` 与新的 `blockedBy`） |
| 目标不存在 | `错误：找不到任务 task_00000000` |
| 依赖不存在 | `错误：找不到依赖任务 task_1b2c3d4e` |
| 目标已被认领 | `错误：task_5a6b7c8d 当前是 in_progress，只能给 pending 且无人认领的任务加依赖` |
| 自依赖 / 成环 | `错误：加这些依赖会形成环：task_5a6b7c8d → task_1b2c3d4e → task_5a6b7c8d` |
| 重复依赖 | 成功返回，`blockedBy` 不出现重复项（幂等） |

上表前三行的**错误文案是【补出】**——原文只规定这四类情况必须被拒（"目标任务和依赖必须存在，
目标必须仍为 pending 且无人认领，并且不能形成自依赖或环"），没有给出消息模板；实现时按项目约定
统一以 `错误：` 开头，并把"缺哪个 ID、当前是什么状态"写进文本。

**"同级调用不共享结果"是本条的关键约束**：一条 assistant 消息里的多个 tool_call 是并发确定的，
`create_task` 的结果要到下一轮才可见。所以建模流程固定为**两阶段**——第 1 轮批量 `create_task`
拿到全部 ID，第 2 轮再用这些 ID 批量 `update_task`。工具描述里必须写清这一点，否则模型会试图
在同一个回复里"创建 A、创建 B、让 B 依赖 A"，而那时 A 的 ID 还不存在。

#### 17.4.3 `can_start`：依赖检查

一个任务只能在它的 blockedBy 全部 completed 之后才能开始：

【原文】

```python
def can_start(task_id: str) -> bool:
    return not incomplete_dependencies(load_task(task_id))
```

incomplete_dependencies 读取每个前置任务。只要有一个不是 completed，或者对应文件已经不存在，任务就不能认领。

| 输入 | 返回 |
|---|---|
| `can_start("task_1b2c3d4e")`（依赖已全部 completed） | `True` |
| `can_start("task_5a6b7c8d")`（有依赖未完成） | `False`（文本结果同形） |

`can_start` 是**只读判定**，也是 `claim_task` / `complete_task` 内部使用的同一判据
（`claim_task` 直接用 `incomplete_dependencies`），因此"工具问出来的答案"与"认领时实际的判定"
不可能分叉。

#### 17.4.4 `claim_task`：认领任务

Agent 开始做一个任务时，调用 claim_task：设置 owner，状态从 pending → in_progress。owner 字段记录谁认领了这个任务：

【原文】

```python
def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    dependencies = incomplete_dependencies(task)
    if dependencies:
        return f"Blocked by: {dependencies}"
    task.owner = owner
    task.status = "in_progress"
    TASKS.save(task)
    return f"Claimed {task_id} ({task.subject})"
```

如果任务不是 pending，或者依赖没有完成，就拒绝认领。S10 只处理顺序执行的状态更新。
（"S10" 是原文的阶段编号，保留原样。）

| 输入 | 返回 |
|---|---|
| `claim_task("task_1b2c3d4e")` | `Claimed task_1b2c3d4e (endpoints)` |
| `claim_task("task_1b2c3d4e")` 再次 | `Task task_1b2c3d4e is in_progress, cannot claim` |
| `claim_task("task_5a6b7c8d")` 依赖未完成 | `Blocked by: ['task_1b2c3d4e']`（依赖列表按原文直接插值，保持 Python 列表字面量形状） |

#### 17.4.5 `complete_task`：完成与解锁

任务做完后，设为 completed。同时扫描所有其他任务，找出刚刚被解锁的下游任务：

【原文】

```python
def complete_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete"
    if task.owner != owner:
        return f"Task {task_id} is owned by {task.owner}, not {owner}"
    ready_before = {t.id for t in list_tasks()
                    if t.status == "pending" and t.blockedBy
                    and can_start(t.id)}
    task.status = "completed"
    TASKS.save(task)
    unblocked = [t.subject for t in list_tasks()
                 if t.status == "pending" and t.blockedBy
                 and t.id not in ready_before
                 and can_start(t.id)]
    msg = f"Completed {task_id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
    return msg
```

完成 "schema" 后，"endpoints" 和 "docs" 的 can_start 返回 True，它们可以开始。

| 输入 | 返回 |
|---|---|
| `complete_task("task_9f3c1a7e")`（schema） | `Completed task_9f3c1a7e (schema)` + 换行 + `Unblocked: <被解锁的 subject 列表>`（顺序即 `list_tasks()` 的顺序，见 §17.3） |
| 无下游被解锁 | `Completed task_9f3c1a7e (schema)`（不带 `Unblocked:` 行） |
| 状态不是 in_progress | `Task task_9f3c1a7e is pending, cannot complete` |
| owner 不匹配 | `Task task_9f3c1a7e is owned by agent-1, not agent` |

`ready_before` 那一步是**只报告"本次新解锁"**：已经把依赖做完、本来就能开始的任务不会被重复报成
"刚被解锁"。没有它，每完成一个任务都会把此前所有可开始的任务再报一遍，模型会误以为产生了新进展。

#### 17.4.6 `get_task`：查看完整细节

list_tasks 只显示一行摘要。get_task 返回完整的任务 JSON，包括 description 和依赖细节。跨会话恢复时，Agent 需要读取完整描述才能继续工作：

【原文】

```python
def get_task(task_id: str) -> str:
    task = load_task(task_id)
    return json.dumps(asdict(task), indent=2)
```

| 输入 | 返回 |
|---|---|
| `get_task("task_5a6b7c8d")` | 完整 JSON（逐字对应 17.2 的数据结构，含 `description` 与 `blockedBy`） |

```json
{
  "id": "task_5a6b7c8d",
  "subject": "deploy",
  "description": "把服务发布到预发环境并跑一遍冒烟",
  "status": "pending",
  "owner": null,
  "blockedBy": [
    "task_1b2c3d4e",
    "task_9f3c1a7e"
  ]
}
```

### 17.5 状态机：两个动作，三个状态

【原文】

```
pending ──claim──→ in_progress ──complete──→ completed
```

这里的 claim / complete 是动作，pending / in_progress / completed 是状态：

* **claim_task**：pending → in_progress。设置 owner，开始工作。
* **complete_task**：in_progress → completed。把任务标记为完成，并解锁下游。

| 动作 | 前置状态 | 后置状态 | 附加效果 | 拒绝条件（原文消息模板） |
|---|---|---|---|---|
| `claim_task` | `pending` | `in_progress` | 写入 `owner` | 非 `pending` → `Task {id} is {status}, cannot claim`；依赖未完成 → `Blocked by: {dependencies}` |
| `complete_task` | `in_progress` | `completed` | 扫描下游，报告新解锁 | 非 `in_progress` → `Task {id} is {status}, cannot complete`；`owner` 不匹配 → `Task {id} is owned by {owner}, not {caller}` |

**本章只定义这两条迁移**：`completed` 不可回退，`in_progress` 不可退回 `pending`
（原文：S10 只处理顺序执行的状态更新）。"重新打开任务""释放认领"属于后续范围，
需要在同一张状态机上显式补边，而不是让某个工具偷偷改 `status`。

### 17.6 依赖关系图

【原文】SVG 源码逐字保留（可直接存成 `.svg` 用浏览器打开）：

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 400" font-family="system-ui, -apple-system, sans-serif">
  <defs>
    <marker id="dep" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/>
    </marker>
  </defs>

  <rect width="760" height="400" fill="#fafbfc" rx="8"/>

  <!-- Title -->
  <rect x="0" y="0" width="760" height="44" fill="#0d9488" rx="8"/>
  <rect x="0" y="36" width="760" height="8" fill="#0d9488"/>
  <text x="380" y="28" fill="#fff" font-size="15" font-weight="700" text-anchor="middle">Task DAG — 依赖关系示例：搭数据库 → API → 测试 → 部署</text>

  <!-- Row 1: schema (completed) -->
  <rect x="295" y="70" width="170" height="48" rx="8" fill="#dcfce7" stroke="#16a34a" stroke-width="2"/>
  <text x="380" y="92" fill="#166534" font-size="12" font-weight="700" text-anchor="middle">✓ schema</text>
  <text x="380" y="108" fill="#16a34a" font-size="9" text-anchor="middle">completed</text>

  <!-- Arrows: schema → endpoints, schema → docs -->
  <path d="M 340 118 L 240 162" fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#dep)"/>
  <path d="M 420 118 L 520 162" fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#dep)"/>

  <!-- Row 2: endpoints (in_progress), docs (pending) -->
  <rect x="115" y="164" width="170" height="48" rx="8" fill="#dbeafe" stroke="#2563eb" stroke-width="2"/>
  <text x="200" y="186" fill="#1e40af" font-size="12" font-weight="700" text-anchor="middle">● endpoints</text>
  <text x="200" y="202" fill="#2563eb" font-size="9" text-anchor="middle">in_progress · owner: agent-1</text>

  <rect x="475" y="164" width="170" height="48" rx="8" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.5"/>
  <text x="560" y="186" fill="#475569" font-size="12" font-weight="700" text-anchor="middle">○ docs</text>
  <text x="560" y="202" fill="#94a3b8" font-size="9" text-anchor="middle">pending · blockedBy: schema ✓</text>

  <!-- Arrows: endpoints → tests, docs → deploy -->
  <path d="M 200 212 L 200 262" fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#dep)"/>
  <path d="M 510 212 L 440 262" fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#dep)"/>

  <!-- Row 3: tests (pending), deploy (pending) -->
  <rect x="115" y="264" width="170" height="48" rx="8" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.5"/>
  <text x="200" y="286" fill="#475569" font-size="12" font-weight="700" text-anchor="middle">○ tests</text>
  <text x="200" y="302" fill="#94a3b8" font-size="9" text-anchor="middle">blockedBy: endpoints ●</text>

  <!-- Arrow: tests → deploy -->
  <path d="M 285 288 L 375 288" fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#dep)"/>

  <rect x="375" y="264" width="170" height="48" rx="8" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.5"/>
  <text x="460" y="286" fill="#475569" font-size="12" font-weight="700" text-anchor="middle">○ deploy</text>
  <text x="460" y="302" fill="#94a3b8" font-size="9" text-anchor="middle">blockedBy: tests, docs</text>

  <!-- Legend -->
  <rect x="40" y="338" width="680" height="46" rx="6" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>
  <rect x="60" y="352" width="14" height="12" rx="3" fill="#dcfce7" stroke="#16a34a" stroke-width="1"/>
  <text x="80" y="363" fill="#475569" font-size="10">completed</text>
  <rect x="160" y="352" width="14" height="12" rx="3" fill="#dbeafe" stroke="#2563eb" stroke-width="1"/>
  <text x="180" y="363" fill="#475569" font-size="10">in_progress</text>
  <rect x="270" y="352" width="14" height="12" rx="3" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1"/>
  <text x="290" y="363" fill="#475569" font-size="10">pending</text>
  <text x="370" y="363" fill="#94a3b8" font-size="10">→ blockedBy（箭头 = 依赖方向）</text>
  <text x="60" y="378" fill="#94a3b8" font-size="9">docs 的 blockedBy (schema) 已完成 → can_start 返回 True，可被 claim</text>
</svg>
```

**读法（箭头方向必须与 `blockedBy` 一致，不能反过来）**：箭头从**被依赖的任务**指向
**依赖它的任务**；箭头终点的 `blockedBy` 里包含箭头起点。于是这张图对应的边是：

| 箭头 | 含义（数据） |
|---|---|
| `schema → endpoints` | `endpoints.blockedBy = ["schema"]` |
| `schema → docs` | `docs.blockedBy = ["schema"]` |
| `endpoints → tests` | `tests.blockedBy = ["endpoints"]` |
| `docs → deploy` | `deploy.blockedBy = ["docs"]` |
| `tests → deploy` | `deploy.blockedBy = ["tests"]` |

按这张图代入 17.4.5 的原文示例：`complete_task(schema)` 之后，"endpoints" 与 "docs" 的
`can_start` 由 `False` 变 `True`（它们的 `blockedBy` 只剩已完成的 schema），所以返回消息里的
`Unblocked:` 恰好是 endpoints 与 docs 两条（顺序按 `list_tasks()` 的排序，断言取集合）；而 "tests"（依赖 in_progress 的 endpoints）与
"deploy"（依赖 tests 与 docs）仍然 `can_start == False`，不在解锁列表里。

### 17.7 集成落点与失败模型

**落点**（待出图时确认，按现有目录约定）：

| 内容 | 位置 |
|---|---|
| `Task` / `TaskStore` / 六个工具实现 | 新增 `src/avid/tools/tasks.py` |
| 六个工具的 schema | `src/avid/tools/schemas.py` 新增定义，沿用 `tool()` 信封 |
| 注册进 `TOOLS` / `TOOL_IMPLS` | `src/avid/tools/__init__.py` |
| 契约与行为测试 | 新增 `tests/test_tasks.py`（+ 契约测试自动覆盖"定义与实现一一对应"） |

任务工具**进** `execution.STATEFUL_TOOLS`：任务状态在文件里，但**工作区根**从 `RunState` 取
（阶段 18 起 `.tasks/` 跟着运行级工作区根走），所以签名与其它工具一致，都是
`(args, *, state)`。

注意一个自动后果：`SUB_TOOLS` 只剔除 `subagent` 本身，所以六个任务工具会**自动进入子 agent 的
工具集**。这既是"分工"能落地的前提（子 agent 自己认领与完成），也正是 17.8 第 1 条必须解决
`owner` 身份的原因——否则子 agent 只能以默认的 `agent` 身份认领，图上看不出谁在做。

**不变量**

| # | 不变量 | 守护者 |
|---|---|---|
| I1 | 一个任务一个文件，文件名即 ID；创建用排他写入，ID 冲突重新生成而不是覆盖 | `TaskStore.create` |
| I2 | 一次 `update_task` 的校验与保存全有或全无（先校验完，再统一保存） | `TaskStore.update_dependencies` |
| I3 | 边只在目标仍为 `pending` 且无人认领时添加，且不得自依赖或成环 → 图永远是无环图 | `update_dependencies` 的校验 |
| I4 | 状态只能沿 `pending → in_progress → completed` 前进 | `claim_task` / `complete_task` |
| I5 | 只有 `owner` 匹配的那个执行者能 `complete` | `complete_task` 的 owner 校验 |

**失败面**：任务文件缺失（`can_start` 为 `False`、`claim` 拒绝、`get_task` 回可读错误）；
JSON 损坏（实现按"拒绝并回文本"处理，契约测试覆盖，不静默当空任务）；状态竞争
（同一 `owner` 顺序执行；**多个 Agent 并发认领同一任务时如何互斥，本章未定义**，需要文件锁或
排他写——标为开放问题）；进程崩溃（任务即时落盘，未做完的任务停在 `in_progress`，需要人工或
后续阶段补"回收/超时释放"）。

### 17.8 未定义、留给实现阶段决定

1. `owner` 的身份来源：默认 `owner="agent"`；阶段 6 的 `subagent` 要区分身份，需要给它分配 id 并把
   它传进工具参数——接线方式待定。
2. 是否给任务工具加审批：`create_task` / `update_task` / `claim_task` / `complete_task` 会写工作区内
   文件，按阶段 3 的闸门可以进 `APPROVAL_RULES`；只读的 `can_start` / `get_task` 不必。
3. 并发认领的互斥机制（文件锁 / 单飞队列）。
4. 损坏任务文件的修复策略（拒绝 vs 隔离到 `.tasks/corrupt/`）。
5. `completed` 的回退与 `in_progress` 的释放（需要在本章的状态机上显式补边）。

### 17.9 验收标准

| # | 标准 | 判定方式 |
|---|---|---|
| 1 | 六个接口与原文签名**逐字一致**（含 `blockedBy` 这个字段名与默认值 `description=""` / `owner="agent"`） | `inspect.signature` 与本章代码块比对 |
| 2 | 落盘形状：一个任务一个 `.tasks/{id}.json`；ID 匹配 `^task_[0-9a-f]{8}$` | 建任务后读目录；正则断言 |
| 3 | 排他写入与 ID 冲突重生成 | 预置同名文件后创建 → 断言生成的 ID 不同、旧文件内容未变 |
| 4 | `create_task` 的初值：`status=pending`、`owner=None`、`blockedBy=[]`；工具结果返回运行时 ID | 读回 JSON 断言；断言工具结果文本等于该 ID |
| 5 | 两阶段构图：同一回复内多个 `create_task` 的 ID **不能**互相引用 | 脚本化对话：第 1 轮并发两个 `create_task` → 断言第 2 轮才能拿到 ID 建边 |
| 6 | `update_task` 四类校验（目标不存在 / 依赖不存在 / 目标非 pending 或已认领 / 自依赖或成环）各自被拒且**磁盘不变** | 逐条断言被拒（错误文案以实现为准）+ 重读文件比对 |
| 7 | 重复依赖幂等：重复 `addBlockedBy` 不产生重复边 | 连续两次同一依赖 → `len(blockedBy)` 不变 |
| 8 | `can_start`：依赖全部 completed 且文件都在 → `True`；任一未完成或依赖文件缺失 → `False` | 按 17.6 的图逐节点断言 |
| 9 | `claim_task`：成功写 `owner` 且 `in_progress`；非 pending / 依赖未完成时返回原文消息 | 断言返回文本与 JSON 状态 |
| 10 | `complete_task`：owner 匹配才成功；只报告**本次新解锁**的下游；`complete_task(schema)` 的 `Unblocked:` 恰为 {endpoints, docs} 两条（顺序按 `list_tasks()`，断言用集合） | 按图跑一遍完整序列并断言消息 |
| 11 | `get_task` 返回 `json.dumps(asdict(task), indent=2)`，含 `description` | 文本与字段断言 |
| 12 | 状态机只有两条迁移；三个状态名在 schema enum、代码与测试里**字面一致** | 契约测试断言 enum；`grep` 断言无第四种状态名 |

## 18. 落地记录（阶段 13，任务图 Task DAG）

§17 的设计稿已实施：`todo_write` 答不了的两件事（**这条现在能不能开工**、**谁在做**）由
`.tasks/` 里的任务图补上。

### 18.1 落地范围

| 文件 | 动作 | 内容 |
|---|---|---|
| `src/avid/tools/tasks.py` | 新增 | `Task` / `TaskStore` / `TASKS` / `load_task` / `list_tasks` / `incomplete_dependencies` + 六个**原文签名**的库函数 + 六个工具外壳 |
| `src/avid/tools/schemas.py` | 修改 | +6 个 `tool()` 定义（含"两阶段构图"的显式说明） |
| `src/avid/tools/__init__.py` | 修改 | 注册进 `TOOLS` / `TOOL_IMPLS`：8 → **14** 个工具；`SUB_TOOLS` 自动变成 13 个 |
| `.gitignore` | 修改 | 加 `.tasks/`（运行期数据，不是源码） |
| `tests/test_tasks.py` | 新增 | 38 项，覆盖 §17.9 的 12 条验收 + 并发/损坏/越界/容错 |
| `tests/test_tools_contract.py` | 修改 | 期望工具名清单 8 → 14 |

全量测试：**499 passed**（阶段 12 为 437，本阶段 +62）。

### 18.2 出图时裁决的五个开放问题，落地成了什么

| # | 裁决 | 落地方式 | 证据 |
|---|---|---|---|
| 1 | `owner` 默认 `"agent"`，子 agent 身份接线不做 | 库函数签名保留 `owner: str = "agent"`；schema 把 `owner` 暴露为可选参数 | `inspect.signature` 与 §17.4 原文一致 |
| 2 | 四个写入工具不进审批闸门 | 未改 `APPROVAL_RULES`；写入被 `TaskStore` 限死在 `.tasks/{id}.json`（ID 正则 + 工作区边界 + 覆盖写只针对已存在的任务文件） | 真实运行 8 次任务调用，0 次审批提示；要翻只需在 `APPROVAL_RULES` 加一行 |
| 3 | 进程内互斥用一把锁 | `TaskStore` 持 `threading.RLock`；`claim_task` / `complete_task` 的"读 → 判断 → 写"由工具外壳整段括起来（RLock 让内部的 `load` / `save` 重入而不自锁） | `test_concurrent_claims_leave_exactly_one_winner`：两个线程抢同一条任务，恰好一个 `Claimed`、一个 `cannot claim`，磁盘上的 owner 是赢家 |
| 4 | 损坏文件在修改路径报错、只在列表路径跳过 | `load` 抛 `TaskError`；`list_all` 记 warning 后跳过；`update_dependencies` 用严格的 `_load_all_strict`，不把损坏当缺失 | `test_list_tasks_skips_corrupt_files_but_load_and_tools_report_them`、`test_update_task_refuses_to_treat_a_corrupt_dependency_as_missing` |
| 5 | `completed` 不回退 | 只有 `claim_task` / `complete_task` 两条迁移，两者都先校验当前状态与 owner | `test_status_terminology_is_exactly_the_three_names`、`test_complete_rejects_wrong_status_and_wrong_owner` |

### 18.3 §17.9 的 12 条验收结果

| # | 标准 | 结果 |
|---|---|---|
| 1 | 六个接口签名逐字一致 | 库函数层原样：`create_task(subject, description="")`、`update_task(task_id, addBlockedBy)`、`can_start(task_id)`、`claim_task(task_id, owner="agent")`、`complete_task(task_id, owner="agent")`、`get_task(task_id)`；`Task` 六个字段（含 `blockedBy`）逐字 |
| 2 | 一个任务一个 `.tasks/{id}.json`，ID 匹配 `^task_[0-9a-f]{8}$` | `test_create_task_writes_one_file_with_all_six_fields`、`test_each_task_is_a_separate_file` |
| 3 | 排他写入与 ID 冲突重生成 | `test_id_collision_regenerates_and_keeps_the_existing_file`：预置 `task_00000000.json` → 新任务拿到下一个 ID，占位文件一字未改 |
| 4 | `create_task` 初值与结果形态 | `status=pending`、`owner=None`、`blockedBy=[]`；工具结果文本**就是**运行时 ID（`test_create_task_writes_one_file_with_all_six_fields`、真实运行里 `→ task_63116301`） |
| 5 | 两阶段构图，同轮 ID 不可互引 | `test_two_phase_construction_through_the_agent_loop`（第 1 轮两个 `create_task`、第 2 轮才用返回的 ID 连边）；`test_guessed_id_cannot_be_used_in_the_same_reply`（凭空写的 ID 得到 `错误：找不到任务 task_deadbeef`） |
| 6 | `update_task` 四类校验被拒且磁盘不变 | `test_update_task_rejects_and_leaves_disk_untouched`（参数化 6 种：目标不存在/依赖不存在/已认领/已完成/自依赖/成环），逐字节比对全部任务文件 |
| 7 | 重复依赖幂等 | `test_update_task_adds_edges_and_is_idempotent`：`blockedBy` 不出现重复项 |
| 8 | `can_start` 的三种结果 | `test_can_start_follows_completed_dependencies`（in_progress 不算完成、completed 才算）、`test_can_start_is_false_when_a_dependency_file_disappears`、`test_can_start_is_false_for_a_missing_task` |
| 9 | `claim_task` 成功与两种拒绝 | `Claimed {id} ({subject})`、`Task {id} is in_progress, cannot claim`、`Blocked by: ['…']`（原文的列表插值形状） |
| 10 | `complete_task` 只报本次新解锁 | `test_complete_reports_newly_unblocked_downstream`、`test_complete_without_downstream_has_no_unblocked_line`、`test_complete_only_reports_tasks_unblocked_by_this_completion`（已完成依赖的任务不重复报） |
| 11 | `get_task` 返回完整 JSON | `test_get_task_returns_the_full_json_including_description`（字段与 §17.2 一致，含 `description` 与 `blockedBy`） |
| 12 | 状态机两条迁移、术语一致 | `test_status_terminology_is_exactly_the_three_names`：三个状态名在 `VALID_STATUSES`、磁盘记录与工具描述里字面一致，无第四种 |

另外补了三条边界测试：任务 ID 不能是路径（`test_task_id_must_match_the_pattern_and_stay_inside_the_directory`、
`get_task` 传 `../../etc/passwd` 得到 `错误：找不到任务`）、`list_tasks` 跳过损坏文件、
`complete_task` 遇到损坏的任务文件回 `错误：任务文件损坏`。

### 18.4 真实运行证据（一次连续运行，5 轮 8 次工具调用，0 次拒绝）

提示词要求模型"先只建三个节点、下一轮再加边"，模型的执行顺序：

```
round=1  create_task ×3           → task_63116301(schema) / task_e3e4df07(endpoints) / task_50e0b08e(tests)
round=2  update_task ×2          → endpoints.blockedBy=[schema]，tests.blockedBy=[endpoints]
round=3  can_start(tests)        → False
         claim_task(schema)      → Claimed task_63116301 (schema)
round=4  complete_task(schema)   → Completed task_63116301 (schema)
                                    Unblocked: endpoints
round=5  （收尾）                 → 模型自己说明"tests 仍被 endpoints 挡住"
```

`.tasks/` 里三条记录的形状与 §17.2 完全一致（`schema` 为 `completed` 且 `owner=agent`、
`endpoints`/`tests` 仍 `pending` 并带着各自的 `blockedBy`）。演示产生的三个任务文件在验收后已删除。

### 18.5 落地时发现的偏差（已回写 §17）

1. **`Unblocked:` 的顺序**：`list_tasks()` 按 id 排序，而 id 是每次运行随机生成的；改用文件 mtime
   也不行——`update_task` 会刷新 mtime，等于用"最后修改"冒充"创建顺序"。因此 §17.4.5 / §17.6 / §17.9
   的示例与验收口径统一改成**集合**，不再假装有稳定顺序。
2. **两种拒绝不是一类东西**：`Task {id} is {status}, cannot claim` 与 `is owned by …, not …` 是
   **正常拒绝**（原文的消息模板），工具结果不加 `错误：` 前缀；只有"任务不存在 / 文件损坏 /
   参数非法"才加。混为一谈会让模型把"状态不符"当成系统故障去重试。
3. **两条写路径**：新建用 `"x"`（O_EXCL）——冲突是正常情况，重新生成 ID；字段更新用
   `.tmp` + `os.replace`——中途失败不能留下半截 JSON。两者都由 `TaskStore` 独占。
4. **列表容错、修改严格**：同一份目录，`list_tasks()` 跳过坏文件，`update_dependencies` 遇到坏依赖
   直接报错。理由不同：列表是"尽量给全貌"，修改是"不能基于缺失的信息做判断"。

### 18.6 回滚与后续

**回滚**：`git revert` 本阶段提交即可（新增文件 + 三处注册 + 契约测试清单）。没有数据迁移问题——
`.tasks/` 是新增目录；回滚后它与 `.gitignore` 里的那条一起变成无用文件，不会被读取。

**未做**（触发条件）：① 子 agent 的 `owner` 身份（需要区分"哪个 subagent 认领的"时，把 actor id
放进 `RunState` 并让工具外壳读它）；② 任务工具进审批闸门（出现任务文件被写成不该写的内容时，
在 `APPROVAL_RULES` 加一行）；③ 跨进程互斥（多进程同时操作同一工作区时改文件锁）；
④ `completed` 回退 / `in_progress` 释放（需要在状态机上**新增动作**而不是直接改 `status`）；
⑤ 给人看的任务列表（`avid --list-tasks` 之类；模型侧已有 `get_task` 与 `list_tasks` 语义）。

## 19. 落地记录（阶段 18，工作区与权限三态）

规格与决策表另有单篇：`docs/design/workspace-permission.md`（命名、默认值、切换方式、危险命令
范围、文案、不变量）。本节只记**模块边界与所有权**这一层的变化，即"为什么改动落在这几个地方"。

### 19.1 新增的两个模块与它们的依据

| 模块 | 负责什么 | 为什么单独成模块（判据 4 / 5） |
|---|---|---|
| `workspaces.py`（顶层，与 `ai/` / `session/` 平级） | `Workspace` 记录、id 派生、用户级注册表 | 它是**索引**，不是真相：会话数据在各自工作区的 `.avid/sessions/` 下，归属在会话 header 里。注册表被删掉不丢任何数据，id 由根目录派生所以重复登记幂等；它还是**只由用户显式动作写**的文件（启动与日常使用都不写盘），因此"注册表里有什么"与"起过几次服务"可区分把它放进 `session/` 会让"会话存储"承担跨工作区的目录知识；放进 `policy/` 会让策略层拥有磁盘布局 |
| `svc/workspaces.py` | 注册表的 HTTP 面 + "按工作区取会话仓库" | 与 `svc/sessions.py` / `svc/runs.py` 平级：它回答"有哪些工作区""这个会话属于哪一个"，两个服务都依赖它，而不是各自扫目录 |

`session/` 保持**零 avid 内部依赖**：它不认识 `workspaces.py`，只接受构造期传入的一个
`workspace` 字符串；"老会话按位置归属"由调用方（仓库持有者）补上。这正是阶段 12 立下的
边界，本阶段没有为了少写两行代码去破它。

### 19.2 模块全局 → 运行级值（本次最大的一处结构改动）

`tools/workspace.py::WORKSPACE_ROOT` 原本是 import 时固定的进程级常量，有 **9 个读取点**。
一个进程要能服务多个工作区，它就必须降级为"没有运行级值时"的默认：

| 读取点 | 现在的来源 |
|---|---|
| 文件工具（`files.py`） | `state.workspace_root`（未设则全局） |
| `bash` 的 cwd（`shell.py`） | 同上 |
| 任务库目录（`tasks.py`） | 同上（`store_for(state)`） |
| 系统提示的"工作目录"（`skills.py`） | `RunState.system_prompt` 传入 |
| 压缩落盘目录（`compaction.py`） | `context.prepare` 传入 `workdir` |
| 注入模型的 `[环境]`（`hooks.py`） | hook context（`execution` 注入） |
| 会话库（`cli.py` / `svc/__init__.py`） | 工作区解析结果 |
| `capabilities.workspace`（`svc/__init__.py`） | 默认工作区根（多工作区模式下是进程默认根） |

**所有权**：运行级根由 `RunState` 拥有，唯一写入者是运行创建方（CLI / `RunRegistry`）；
工具只读，不推导、不缓存。**失败模型**：读不到运行级值就回落到进程默认根，而不是报错——
单工作区路径（CLI 交互、既有测试）因此逐字不变（`tests/test_run_workspace.py` 有一条
显式断言这个回落）。

**任务库的锁**：任务库从"进程级单例"变成"每个运行一个实例"，于是互斥必须换锚点——
锁按**目录**共享（`_lock_for(directory)`），否则同一个工作区里并发运行会各自持有不同的锁，
`claim_task` 的三步读-改-写不再互斥。这是"实例化位置变了、互斥语义没变"的必要修正。

### 19.3 权限层的边界

四层裁决（硬拒绝 → 危险命令 → 越界 → 常规规则）收敛在 `policy.permission.gate` 一个入口，
`runtime/hooks.py` 只负责**算事实**（危险类别来自 `danger_reason`，越界目标来自
`tools/workspace.outside_target` / `outside_command_target`）并把运行级上下文注进去。

- **路径数学只有一份**（`tools/workspace.py`）：策略层不复制边界判断，所以"判定在区外"与
  "执行时拒绝"不可能给出不同答案（判据 6：同一不变量的守护者只有一个）。
- **文件工具只读账本、不做决定**：`resolve(..., outside_ok=state.outside_allowed)`。
  没有授权就回绝，于是"失败关闭"不取决于询问是否发生。
- **`AskUser` 签名保持三参数**：分类与目标写进 `reason` 文本，Web 的审批表与 CLI 的
  stdin 回答者因此都不必改。代价是审批卡片拿不到结构化的风险字段——触发条件写在
  `workspace-permission.md` §5.3（需要按风险分色时再加）。
- **`--yes` 只换回答者**：它不改"哪些动作会打问号"（模式决定），也不越过硬拒绝。

### 19.4 适用条件与失效信号

- **成立条件**：单机、单人、工作区是本地目录、会话 id 全局唯一。
- **失效信号**：① 按会话定位变慢（每次要扫各工作区的 `repo.list()`）→ 把工作区 id 编进
  会话 id 或加索引；② 出现"以为建在 A、实际建在 B"的事故 → 建会话已一律必填归属，
  届时查预选规则（`is_default` → 最近使用）是否误导；③ 审批卡片需要按风险分色 / 分组 → 加结构化的 `warning` / `target` 字段；
  ④ 多机或多人共享工作区 → 注册表与文件锁都要重做（本设计明确不覆盖）。
