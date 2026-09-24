# Avid 整体架构

本文件是**总览**：分层、模块职责、依赖方向、数据流、不变量与失败模型，一句话一张表，
每条给证据。它**不重复** `docs/design/` 里已有的论证——阈值取舍、方案对比、判据推导、
落地记录与「重新考虑信号」都在那里，本文件只指路。

**复用关系**（写的时候刻意不复制这些内容）：

| 已有文档 | 它负责什么 | 本文件怎么用它 |
|---|---|---|
| `docs/design/runtime-architecture.md` | 内核四层怎么推出来的、每处边界的代价、判据 9 的措辞修正、阶段 A/B/12/13/18 落地记录 | 引用其 §3 分层表与 §6 不变量 I1–I6，不重写 |
| `docs/design/frontend-architecture.md` | Web 层与前端怎么选型、事件三档为什么这么分、L0–L4、性能预算、17 条未验证假设 | 引用其 §0 决策速览、§3.1 分层表、§11 不变量 I1–I15 |
| `docs/design/workspace-permission.md` | 工作区、三轴预设、四级 deny 阶梯、沙箱与审计的决策表与文案 | 只写它在整体里占哪一层 |
| `docs/design/architecture-criteria.md` | 12 组检查点（分析时用的尺子） | 只标注哪些检查点被覆盖 |
| `docs/guide/web-ui.md` | 页面 ↔ 接口对应、SSE 消费规则、验证命令 | 引用，不复制端点表 |

**更新时机**：分层、依赖方向、数据所有权、不变量或门禁变化时。

---

## 1. 分层总览

`src/avid/` 下七个包（`ai` / `runtime` / `policy` / `session` / `svc` / `web` / `tools`）
加两个顶层模块（`cli.py` / `workspaces.py`），再加仓库根的前端目录 `web/`。依赖方向自上而下
（详细断言见 §4）。这张表是 `docs/design/runtime-architecture.md:47-68`
（内核四层）与 `docs/design/frontend-architecture.md:87-106`（Web 两层）的合并视图，
并补上两份设计文档成文后才出现的 `session/`、`workspaces.py`：

| 层 | 单元 | 一句话职责 | 它拥有什么数据 | 允许依赖 |
|---|---|---|---|---|
| 应用 | `cli.py` | 终端接线：参数、stdin 审批、stdout 输出、退出码 | 无（只有接线） | runtime、session、ai、policy、tools、workspaces、web.app |
| 应用服务 | `svc/` | 内核的**第二个调用方**：运行注册表与生命周期、事件缓冲与重放、审批待决表、会话读视图、任务只读视图 | 进程内的 run 记录与事件缓冲、待决审批、SSE 额度 | runtime、session、tools、policy、ai、workspaces |
| 传输适配 | `web/` | HTTP 路由、pydantic DTO、SSE 编帧、静态资源与 SPA fallback | 线格式（DTO/错误信封/编帧） | svc、runtime.events、session 类型 |
| 运行时 | `runtime/loop.py` | **只表达调度顺序**：什么时候调模型、什么时候跑工具、什么时候停 | 无 | runtime 其它、ai、tools |
| 运行时 | `runtime/context.py` | 上下文管线的**编排**（五步什么时候跑、什么顺序） | 无 | policy.compaction |
| 运行时 | `runtime/execution.py` | 工具调用协议：解析 → 拦截 → 执行 → 回填 | 无 | events、tools（**无 policy**） |
| 运行时 | `runtime/state.py` | `RunState`：一次运行的可变状态与生命周期 | 轮次计数、一次性标志、统计、账本、工作区根、**运行级安全规格 `RunSecurity`**、运行期实例（todo/skills/hooks） | policy.permission / policy.skills / policy.todo |
| 运行时 | `runtime/hooks.py` | 扩展点的注册与触发（四事件） | 默认回调注册表 | policy.permission、policy.compaction |
| 运行时 | `runtime/events.py` | 事件名的**唯一单点** + 客户端可见时间常量 | 无 | 无 |
| 策略 | `policy/*` | 阈值、规则与文案（高频变化集中地） | 无（纯函数 + 常量） | ai、tools（延迟） |
| 协议 | `ai/*` | OpenAI 兼容协议、`Config`、`Transcript` | messages 的内存权威 | 无 |
| 会话 | `session/` | 条目树 / 值 / 分支 / 变更线 / 两后端 / 投影 | **磁盘上的会话真相**（JSONL） | 无（零 avid 内部依赖） |
| 能力 | `tools/*` | 15 个工具的 schema 与实现 | 无（写文件系统、进程与外部检索 API） | policy.todo、ai（`subagent`） |
| 顶层 | `workspaces.py` | 用户级工作区注册表（**索引，非权威**） | `~/.avid/workspaces.json` | 无 |
| 前端 | `web/`（仓库根，源码在 `web/src/`） | 全部浏览器代码 | 界面域状态（localStorage） | `web/src/api/`（唯一网络出口） |
| 评测仪器 | `benchmarks/`（仓库根，**不进 wheel**） | AvidBench：case 加载、工作区物化、变体装配、判定器、报表与轨迹落盘 | 只读 case / fixture 与 `runs/` 结果（本地，不入库） | `avid` 的**任意层**——它是叶子消费者，只经既有注入点驱动内核（A14）；产品代码反向不许依赖它 |

边界依据（每条来自设计文档，不在此重推）：`web/`↔`svc/` 隔离**传输形态**；`svc/`↔内核隔离
**多一个调用方**（内核不知道有几个调用方）；前端↔内核隔离**语言与部署单元**，契约是唯一耦合面；
`session/` 零内部依赖，隔离**持久化格式**（路径与时钟构造期注入）；`workspaces.py` 只做索引，
隔离**跨工作区的目录知识**——放进 `session/` 会让会话存储承担它不该有的知识
（`frontend-architecture.md:104-106`、`session/__init__.py:19-20`、`runtime-architecture.md:1221`）。
`benchmarks/` 是**叶子消费者**：它必须能 import 内核的任意一层（否则量不到真实行为），反过来
产品代码一行都不许 import 它——这条由 A14 钉住，因此「无环」不靠自觉。

它里面**故意**有第二份循环（`benchmarks/avidbench/bare.py`）：那是 `bare` 基准线，用来回答
「Avid 的机制比朴素循环强在哪」。A3 的调用点计数只覆盖 `src/`，所以它落在边界外；新加的
A14 则保证这份循环永远只是**消费者**，不会被产品路径引用。

## 2. 一次运行的数据流

两个**平级**接线点，内核不知道有几个调用方（`svc/__init__.py:1-10`）：

```
CLI:  avid --agent / --session
        └─ messages_for_branch(session) + 本次输入 ─┐
Web:  POST /api/sessions/{id}/runs                │
        └─ RunRegistry.start → 线程 → _run() ──────┤
                                                   ▼
                                   agent_loop(messages, tools, system,
                                              chat/summarize, hooks, ask,
                                              on_event, on_message, state)
                                                   │
        ┌──────────────────────────────────────────┴───────────────────────────┐
        │ 1. Transcript(messages)          ← messages 的唯一所有者             │
        │ 2. RunState.for_run(...)         ← 权限模式/账本/工作区根/技能/hooks   │
        │ 3. system_prompt = state.system_prompt(system)                       │
        │ 4. _submit_input → UserPromptSubmit ──► 注入并进系统提示词（不落库）   │
        │ 5. emit(触发用户消息)                                                 │
        │ 6. for round in 1..8:                                                │
        │      state.check_cancelled()          ← 检查点 1（每轮开始前）         │
        │      state.todo_reminder()            ──► TODO_REMINDER + 消息        │
        │      context.prepare(transcript)      ← 五步阶梯（①②/③/④）           │
        │      chat(...) → Turn                 ──► assistant 消息 + RUN_STATUS │
        │      无 tool_calls → Stop hook → return text                         │
        │      有 tool_calls → check_cancelled() ← 检查点 2（每批工具前）        │
        │           execute_batch(tool_calls, schemas=发给模型的同一份定义)      │
        │             ├ PreToolUse  → brokerize → engine.decide → TOOL_CALL_*  │
        │             ├ handler(args, state=…)                                 │
        │             └ PostToolUse → 截断落盘 / 重复提醒 / 日志                 │
        │           ──► tool 消息回填 transcript                                │
        └──────────────────────────────────────────────────────────────────────┘
                    │                                   │
       CLI 落库: on_message → SessionRecorder      Web 额外: on_event → RunRegistry
                    │                                   │
                    ▼                                   ▼
        <工作区>/.avid/sessions/*.jsonl        RunRecord.events（内存重放缓冲）
        （durable 的真相；事件不落这里）         → SSE → 前端 reducer
```

要点（每条都有代码证据）：

- **`on_message` 是消息的唯一出口**：本次运行产生或改写的每条消息按发生顺序回调一次；
  循环不 import 会话层，落库与否由回调决定（`loop.py:136-142`）。
- **`on_event` 是步骤级事实的通道**，不是第二个消息通道（`loop.py:141-142`）。
- **事件不写进会话 JSONL**：durable 事件只进进程内重放缓冲，transient 与 delta 也在同一队列但
  `seq=None`、不参与重放；会话文件只收消息条目（`svc/runs.py:52-69, 325-328, 503-521`、
  `session/recorder.py:53-55`）。
- **`schemas` 用发给模型的那一份** `parameters`，不另抄一份 schema，两边不可能漂移
  （`loop.py:275-285`）。
- **两个检查点是唯二的取消落点**，保证「不丢已产生的消息、也不产生伪造的工具结果」（不变量 I9）。

## 3. 状态与所有权

| 数据 | 谁是权威 | 谁可写 | 生命周期 |
|---|---|---|---|
| messages（内存） | `ai/transcript.py`（唯一所有者） | 只有 `append` / `append_many` / `set_content` / `replace_all` / `splice`；结构变更前先校验候选 | 一次 `agent_loop` 调用 |
| 运行期可变状态 | `RunState`（每次运行一份） | 循环写轮次与统计；`context.prepare` 写 `compacted`；循环写 `retried`；hook 写 `repeat_calls` | 一次运行 |
| 审批账本 | `RunState.ledger`（内存） | `policy.permission.gate`；子 agent 与父运行**共用一本** | 一次运行 |
| 工具同意/拒绝的最终决定 | `policy.engine.decide`（门面 `policy.permission.gate`） | 只有它写账本——`RunState.outside_allowed` 只读结果、不做决定（失败关闭）；沙箱 argv 由 `policy.sandbox` 按账本里的能力授予组装 | — |
| 会话条目 | `session/` 的存储层 | `SessionRecorder` 是唯一写入者（A11 门禁）；条目提交后不可变 | 磁盘，跨进程 |
| 分支 | 一个值（`avid.branch.tip`），不是一张表 | `create_branch` / `append_message` 的提交 | 磁盘 |
| 任务 | `<工作区根>/.tasks/{id}.json` | `TaskStore`（六工具的唯一入口）；Web 只读 | 磁盘，跨会话 |
| 工作区注册表 | `~/.avid/workspaces.json`（**索引**，非权威） | 只由用户显式动作写 | 用户级 |
| 运行记录与事件缓冲 | `svc/runs.py` 的 `RunRegistry`（内存） | 唯一发射线程分配 `seq` | 进程内；终态保留 600s / 最多 200 个 run |
| 待决审批 | `svc/approvals.py`（内存） | 审批队列；超时/取消/结束/重启四条路径全收敛 `deny` | 一次运行，超时 120s |
| 界面域状态 | 浏览器 localStorage | 前端 | 用户清除 |

`RunState` 的字段即「一次运行的全部可变量」（`src/avid/runtime/state.py`）：`auto_approve`、
`permission_mode`（三轴预设名）、**`security`（`RunSecurity`：三轴 + 阶梯 + 沙箱 + 审计）**、
`ledger`、`workspace_root`、`ask`、`observer`、`cancelled`/`cancel_reason`、
`round`/`rounds_since_todo`/`stop_blocks`、`compacted`/`retried`、`tool_calls`/`denials`/`compactions`/`tokens`、
`repeat_calls`、`run_tag`、`todo`、`skills`、`hooks`。两个**一次性标志**各只有一个写入点
（`compacted` 在 `context.prepare`、`retried` 在循环），这是「自动压缩 ≤1 次、兜底 ≤1 次」的守护者。

## 4. 依赖方向与门禁

边界不靠约定，靠**会失败的断言**（`tests/test_web_boundaries.py`，注释原文：这些规则的价值在于
它们**会失败**）。要改边界，必须先改测试与设计文档 §12 判据 9。

| 门禁 | 钉住什么 | 位置 |
|---|---|---|
| A1 | 内核六包（`ai`/`runtime`/`policy`/`session`/`tools`/`svc`）不出现 `fastapi`/`pydantic`/`starlette`/`uvicorn` | `test_web_boundaries.py:58-59, 76-79` |
| A2 | `fastapi` 只允许出现在 `web/`；`uvicorn` 只允许出现在 `cli.py` | `:62-73` |
| A3 | 循环只表达调度：只有 2 个 hook 触发点、无手写 `while`、`agent_loop` 的调用点固定为 4 个文件（定义、CLI、svc、subagent）；svc/web 不按轮次自推调度 | `:85-111`（计数只覆盖 `src/`；包外第 5 个调用点见 §1 的叶子消费者说明） |
| A4 | `svc/` 不 import `web/` | `:117-118` |
| A5 | `LLMError` 一类的内核异常只在 `svc/runs.py` 被捕获并映射 | `:121-126` |
| A6 | 事件名字面量只允许出现在 `runtime/events.py`（其余用常量） | `:132-141` |
| A10 | `on_message` 的接线只允许在 4 个文件（循环、recorder、CLI、svc） | `:144-154` |
| A11 | `web/`、`svc/` 里不出现 `append_message` / `.commit(`——recorder 是唯一写入者 | `:157-162` |
| A12 | 前端在 `src/api/` 之外不直连第三方 URL（`__tests__/` 夹具豁免） | `:168-177` |
| C22 / 对比度 / 体积 | 前端侧：字体声明即加载 + 层级只用 `--z-*`（`check:tokens`）；token 表里声明的对比度配对按 alpha 合成 ≥4.8（`check:contrast`）；首屏 JS / 样式表 / 字体 / 纹理字节（`gate:size`，预算见 `web/budget.json`） | `web/scripts/*.mjs`，命令与口径见 `web/README.md` |
| A13 | `runtime/` → `policy/` 的边**双向**钉住（见下表） | `:245-276` |
| A14 | **产品代码不许 import `benchmarks`**；仪器留在 `src/` 之外，`runs/` 不入库 | `:311-328` |
| 事件契约 | 内核 `EVENT_TYPES` 与前端联合类型成员集合相等；三档声明一致；心跳/兜底常量三处同一个对象 | `tests/test_event_contract.py` |
| 线格式契约 | 22 对 pydantic DTO ↔ 前端 TS interface 的字段名双向相等；真实载荷覆盖每个声明字段 | `tests/test_wire_contract.py` |
| 会话门面 | `session.__all__` 恰好是那份清单；内部件不进 `__all__` 但可子模块导入 | `tests/test_session_facade.py` |
| 工具契约 | 定义与实现一一对应；`STATEFUL_TOOLS` == 真接受 `state=` 的 handler；审批规则只点名已注册工具；`--agent` help 与注册表一致 | `tests/test_tools_contract.py` |

**A13：`runtime/` → `policy/` 的三条边（只读数据，不是豁免名单）**
（`test_web_boundaries.py:233-242`、`docs/design/runtime-architecture.md:456`）：

| 文件 | 允许的 policy 依赖 | 理由 |
|---|---|---|
| `runtime/loop.py` | **零运行时依赖**（`AskUser` 只在 `TYPE_CHECKING` 下） | 调度不该认识策略 |
| `runtime/execution.py` | **零运行时依赖** | 工具协议不该认识策略 |
| `runtime/context.py` | `policy`、`policy.compaction` | 编排压缩 |
| `runtime/state.py` | `policy.permission`、`policy.skills`、`policy.todo` | 持有运行期实例 |
| `runtime/hooks.py` | `policy.permission`、`policy.compaction` | 注册默认回调（权限裁决 + 截断落盘共用同一个 `spill`） |

设计文档 §12 判据 9 对这个边界做过**三处措辞修正**（`:456`）：① `hooks.py` 必然要 import
`policy.permission` 才能注册默认回调——搬代码换不来隔离，所以改的是判据；② 只用于注解的类型
别名走 `TYPE_CHECKING`，不算运行时依赖（判定用 AST 而不是 grep，因为正则分不出这个区别）；
③ `hooks.py` 的 `policy.compaction` 不是新松动——截断落盘与压缩共用同一个函数，复制进
`runtime/` 才是两套真相。

## 5. 事件层与传输契约

- **唯一手写清单**是 `EventType` 这个 `Literal`（18 个成员），`EVENT_TYPES` 由 `get_args` 派生，
  「联合类型里有、清单里没有」这种分叉不可能出现（`runtime/events.py:77-99`）。
- `RunEvent` 是**普通 dataclass**（不是 pydantic）：内核类型保持不可知传输层；`run_id`/`seq`/`ts`
  由 `svc/runs.py` 的唯一发射线程填写（`events.py:13-15, 126-138`）。
- 三档由 `DURABLE_EVENT_TYPES` 一个集合表达（`events.py:7-11`）：durable 带 `id`/`seq` 可重放；
  transient 不带 `id`；delta 不带 `id` 且默认不投递（消费方用 `?deltas=1` 显式订阅）。
- 重放纪律：`after` 是客户端已知的最大 durable `seq`；`_has_gap` 为真或跟随时游标被淘汰 →
  先发一条 durable `resync`；delta 不重放也不占 durable 预算（`svc/runs.py:378-483`）。
- **用量快照走既有事件**（阶段 22）：每轮 `run_status`（transient）与终态 `run_finished`
  （durable）带同一份 `RunState.usage_report()`；`GET /api/runs/{id}` 与分支列表的
  `BranchOut.usage` 读的是同一份计算（`runtime-architecture.md` §20）。加它没有新增事件类型。
- **客户端可见的时间常量只有一个出处**（`events.py:108-118`）：`/api/meta` 公布的
  `stream.heartbeat_seconds`、SSE 生成器的心跳、前端据此设的超时必须是同一个数，否则
  「客户端等得比心跳久」这类错位只能靠人发现。

## 6. 不变量与守护者

内核六条来自 `docs/design/runtime-architecture.md:295-306`：

| # | 不变量 | 守护者 |
|---|---|---|
| I1 | 每个 assistant 的 `tool_calls` 都有配对的 `tool` 结果 | `ai/transcript.py`（唯一写入方法 + 候选校验） |
| I2 | system prompt 不写进 `messages` | `loop`（只有它构造 `system=`） |
| I3 | 压缩前后结构合法 | `Transcript.replace_all` / `splice` 先校验后落地 |
| I4 | ①②③ 步不触达模型 API | `context.prepare` 的签名（`summarize` 只传给 ④）——类型上不可达 |
| I5 | 自动压缩 ≤1 次、兜底 ≤1 次 | `RunState.compacted` / `RunState.retried`，各只有一个写入点 |
| I6 | 工具失败不中断循环 | `execution.execute_batch`（失败转文本） |

「循环不 import 会话层」这一条在 `loop.py:139` 的 docstring 与
`docs/design/frontend-architecture.md:821`（记作「既有 I7」）里被称作 **I7**，但内核设计文档
§6 的表只列了 I1–I6——引用时按本条说明，不要把它当成 §6 的第七行。

Web 与前端十五条来自 `docs/design/frontend-architecture.md:810-828`（I1 条目提交后不可变、
I2 一条消息只出现一次、I3 一个会话至多一个活动 run（**仅进程内**）、I4 durable `seq` 严格单调、
I5 不丢：要么重放要么显式 `resync`、I6 未决审批默认拒绝、I7 内核不 import Web 框架、
I8 内核不 import 会话之外的东西来持久化、I9 取消不丢消息也不产生伪造工具结果、
I10 前端不复制内核判断、I11 对象身份由服务端生成、I12 乱序收敛（delta 必须先 flush 再渲染
durable）、I13 权威终止以注册表 + 已提交条目为准、I14 列表必须有界、I15 delta 不落盘）。

## 7. 失败与恢复

| 层 | 失败 | 现行机制 |
|---|---|---|
| 工具 | 参数非 JSON / 非对象 / 未知工具 / schema 不符 | 回文本、不抛、**不触发事件**、不计入 `tool_calls` |
| 工具 | 实现抛异常 | `工具执行失败：{name}（{exc}）；不要用同样的参数重复调用…`，循环继续 |
| 工具 | PreToolUse / PostToolUse 拦截 | 计 `denials` + `tool_call_denied`；结果替换为拒绝文案 |
| 模型 | `LLMError`（4xx/5xx、非 JSON 响应） | 上抛终止；svc 映射 `run_failed{code:"llm_error"}` |
| 模型 | `PromptTooLongError` | 兜底压缩并重试**一次**（`state.retried` 守护） |
| 模型 | 配置错误 | `run_failed{code:"config_error"}` |
| 会话 | `SessionError` | `run_failed{code:"session_error"}` |
| 程序 | 任何其它异常 | `run_failed{code:"internal"}`，绝不静默死线程 |
| 权限 | 策略拒绝（`decision.kind` 七档） | 回文本按类分档（硬拒绝 / 凭据 / 策略 / 危险 / 越界 / 降级 / 成本）给不同下一步 |
| 权限 | 审批超时/取消/结束/重启 | 一律 `deny`（失败关闭） |
| 压缩 | 落盘失败 | 记日志、跳过本次压缩，不抛；工具输出截断退回「只留头部」 |
| 压缩 | 摘要调用失败 | 记日志、保留原历史；**程序错误不吞**（只捕 `LLMError`/`OSError`/`ValueError`） |
| 压缩 | 找不到安全切口 | 跳过本轮裁剪 |
| hook | 回调抛异常 | 按阻断处理（失败关闭），不短路其它回调 |
| hook | 回调**不返回**（挂住） | **无机制**：`hooks.py` 里零 `timeout` 命中——挂住的 hook 会永久挂住整个运行 |
| JSONL | 末行残片 | 当残片丢弃并原子重写；**中间行非法报错带行号** |
| JSONL | 短写 / fsync 失败 | 校验写入长度，短写截回原大小再抛 `SessionStorageError` |
| JSONL | 跨进程并发 | 旁挂 `.lock` 的 flock；同一存储多线程 close 有独立小锁 |
| 取消 | `RunCancelled` | 只在步骤边界抛出；已产生的消息在产生时就已落库，无需补偿写 |

**明确不做**（设计文档 §7 的「不做」清单）：崩溃恢复 / checkpoint / 重放、失败重试队列、
补偿操作。

## 8. 已知破例、漂移与失效信号

**刻意保留的破例**（不是待修项）：

1. `runtime/hooks.py` 依赖 `policy.compaction` 的 `spill`——截断落盘与压缩必须共用同一个函数
   （同目录、同一句「用 read_file 读回」），复制一份才是两套真相。
2. `runtime/state.py` 持有 policy 的**实例**（`TodoList` / `SkillLoader` / `HookRegistry`）——
   运行期状态的生命周期归它，换掉就是退回隐式全局。
3. `policy/compaction.py` 与 `policy/skills.py` 对 `tools/` 是**延迟导入**——避免包级环，不是边界松动。

**文档漂移**（写文档时发现，改代码或改文档都可以，但要处置）：

1. `docs/design/frontend-architecture.md:3` 仍写「状态：设计，未实施（本文写作时仓库代码零改动）」，
   而 F3/F4 已落地（`features.deltas = 1`、`features.branches = 1`）——状态行未同步。
2. 同一文档 §5.2 的 durable 事件示例表漏了 `tool_result_message`（`runtime/events.py:29` 有它）。
3. `.github/workflows/ci.yml` 的注释仍写「verify 里没有 tsc」，而 `web/package.json` 的 `verify`
   现在含 `typecheck`——CI 因此多跑一次 tsc。
4. `docs/design/runtime-architecture.md:55` 的分层表仍带删除线行 `~~runtime/transcript.py~~`
   并注明归 `ai/`；§3 的层表也未收 `session/`/`svc/`/`web/`/`workspaces.py`（它们分别记在
   §16.3 与 §19.1）。
5. 同一文档 §16.6 的「未做」清单仍列着「文件锁（两个进程可能同时写同一会话文件时）」，
   而它已落地（旁挂 `.lock` 的 flock）——触发条件达成了，段落没回写。
6. `web/src/state/runStore.ts` 的 `useRunSelector` 零消费者——按判据 §2 的删除测试，没有
   调用点的抽象不该留。

**失效信号**（架构在什么条件下不再成立，逐条来自两份设计文档的「适用条件与失效信号」）：

- 单进程、单线程运行、工具同步、一次运行一个会话、单工作区——这是成立条件。
- 出现多用户或远程访问（要鉴权与工作区隔离）、需要跨进程恢复运行中的 run、需要真正的实时
  协作、条目量级到 10⁵ 且要全文检索——这些条件下当前架构不覆盖。
- 观察信号：缓冲淘汰频繁触发 `resync`；同源连接数成为真实瓶颈（同时运行会话 >5）；注入 1000 条
  后 p95 帧超 33ms（需要虚拟化）；事件类型超过 25 个（升级到契约生成）；出现第二个前端表面
  （升级为独立 IDL）；出现第二条「错误文本」约定（给 `ToolOutcome` 加 `status`）。

## 9. 12 组判据的覆盖情况

按 `docs/design/architecture-criteria.md` 的检查点，本文件覆盖情况：

| 判据 | 覆盖 |
|---|---|
| 1 变化优先 | 部分——变化清单与优先级在 `runtime-architecture.md` §1 与 `frontend-architecture.md` §1 |
| 2 具体先行 | 部分——删除测试结论在两份设计文档 §2 |
| 3 耦合 | ✓ §1 边界依据、§4 依赖门禁 |
| 4 边界与决定权 | ✓ §1 分层表 + §3 所有权表 |
| 5 数据所有权与状态生命周期 | ✓ §3 |
| 6 不变量 | ✓ §6（只列已成文的，不新造） |
| 7 失败与恢复 | ✓ §7 |
| 8 并发与一致性 | ✓ §5（重放/淘汰）、§7（锁与取消） |
| 9 依赖与不可靠边界 | 部分——外部依赖只有 LLM API 与文件系统；不可靠边界处置见 §7 |
| 10 边界代价与权衡 | 未覆盖——代价论证在两份设计文档的 §10 |
| 11 可逆性与决策强度 | 未覆盖——见 `runtime-architecture.md` §10.2 |
| 12 运行与演进 | ✓ §8 失效信号 |

## 10. 细节去哪看

| 想知道 | 去哪 |
|---|---|
| 内核四层怎么推出来的、每处代价、阶段 A/B 落地记录 | `docs/design/runtime-architecture.md` §1–§15 |
| 会话持久化的取舍与偏差 | 同上 §16 |
| 任务图的数据结构与状态机设计 | 同上 §17–§18 |
| 工作区与安全分层（三轴 / 阶梯 / 沙箱 / 审计） | `docs/design/workspace-permission.md`；阶段 18 的落地记录见 `runtime-architecture.md` §19，阶段 26 见 `docs/status/CAPABILITIES.md` §3.1 与 `benchmarks/sandbox_boundary/README.md` |
| Web 层选型、事件三档、L0–L4、性能预算、未验证假设 | `docs/design/frontend-architecture.md` |
| 页面 ↔ 接口对应、SSE 消费规则、验证命令 | `docs/guide/web-ui.md` |
| 能力清单与参数细节 | `docs/status/CAPABILITIES.md` |
| 性能与效果数字现状 | `docs/status/BENCHMARK.md` |
| 未来方向 | `docs/status/ROADMAP.md` |
