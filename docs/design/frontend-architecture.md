# Avid 内核 ↔ 浏览器接口面

**状态**：本文件只承载内核与浏览器之间**交换什么**——端点、事件、载荷、错误、时序，以及契约的版本与漂移门禁。浏览器内部怎么组织、长什么样，不在本文件范围内。
**阶段 32**：旧前端（`web/src/**`，123 文件 / 10,433 行：页面、组件、样式、静态资源）已整体删除，它内部的设计（分层与目录结构、组件边界、状态域、视觉语言、性能与验收门禁）随之一并删除，见文末墓碑。现在 `web/` 只剩入口 `main.tsx`、占位壳 `App.tsx`、一条 smoke 测试，以及两份**契约种子** `api/types.ts`、`events/types.ts`。**新的前端设计与视觉语言尚未确认**，本文件不预设。
**依据**：取舍按 `docs/design/architecture-criteria.md` 的检查点推导；每条取舍写「解决了什么 / 牺牲了什么 / 在什么条件下成立 / 什么信号出现时重新考虑」。
**证据来源**：仓库内文件用 `path:line`；调研结论用 `dev/research/*.md:line`（过程文档，不入库，引用时同时写明样本与 commit）。2026-09-22 清理冗余调研产物时删除了 `agent-frontend-survey-addendum.md` 与 `agent-frontend-impl-survey.md` 两份并发稿，下文引用它们的锚点已就地标注为不可复核；合并定稿 `agent-frontend-survey-final.md` 与详情分册 `agent-frontend-survey-deepdive.md` 保留。
**前置阅读**：`docs/design/runtime-architecture.md`（内核四层与不变量 I1–I7）、`dev/plan/roadmap.md`（已完成阶段）。

---

> **§0–§3 已于阶段 32 随旧前端一并删除。** §0–§3：结论速览、变化清单与优先级、抽象准入与删除测试，以及分层、目录结构、组件边界与状态域划分——描述的是已被删除的那份前端的内部结构。

---

## 4. API 方案选择

### 4.1 五个候选（逐项对比）

| 方案 | 依赖增量 | 契约 | 阻塞式人机交互 | 增量文本 | 主要代价 |
|---|---|---|---|---|---|
| **FastAPI + uvicorn** | pydantic / starlette / uvicorn / anyio 等 | 白得 OpenAPI，可生成 TS | ASGI 上层，等价实现 | `StreamingResponse` | 内核首次引入框架依赖簇 |
| Starlette 单用 | starlette / anyio（少 pydantic） | 无 OpenAPI，手写契约 | 同上 | 同上 | 复现调研「手写两份类型」流派的维护面（Chainlit / Continue / Flowise，`dev/research/agent-frontend-survey-final.md:170`） |
| 标准库 `http.server` / 裸 asyncio | 零 | 无 | 手写线程与唤醒 | 手写分块 + 手写 SSE | 长任务、审批往返、流式全部自己实现，成本落在最没有余量的地方 |
| Chainlit | chainlit 及其前端运行时 | 无 schema，事件名两侧手写（`:435`） | `sio.call` 带 ack（`:436`） | 有，但每 token 递归重建消息树并重解析 Markdown（`:345`） | UI 由框架拥有；把 UI 变成内核的依赖而不是内核的消费者 |
| Streamlit（Aider 的路） | streamlit + 生态 | 无 | 整页重跑模型下很别扭 | 无组件级流式 | 交互模型是整页重跑；只适合「能看到循环在跑」这一种目标（`dev/dev/research/agent-frontend-survey.md:327-331`） |

补充两个**前端侧协议**候选：Vercel AI SDK 数据流（AutoGPT、Dify 用）与 AG-UI（Langflow 用 `@ag-ui/client`，`dev/research/agent-frontend-survey-final.md:161`）。它们解决的是「前端怎么消费 agent 流」，若采用就得让 Python 侧产出第三方协议形状的事件，等于把 Avid 自己的事件类型让位给外部协议。**不采用**；重新考虑的信号是出现第二个前端表面——那时才值得把事件定义升级为独立 IDL（Cline 的 22 个 `.proto` / 223 rpc 是那条路的成本）。 

### 4.2 决策：FastAPI

**理由（按证据强度）**

1. 调研内 5 个 Python 后端的 agent 项目全部是 FastAPI/Starlette 系：OpenHands agent-server（`fastapi>=0.104` + uvicorn + websockets）、Open WebUI（已验证 FastAPI，生产关掉 `docs_url`/`openapi_url`）、Chainlit（FastAPI + Starlette + python-socketio）、AutoGPT（FastAPI，且拆成 REST 与 WS 两个 app）、Onyx（FastAPI + 反代 `/openapi.json`）。这是唯一有可复用证据面的选择，其余候选都要靠推断。
2. OpenAPI 自动产出直接满足「契约先于 UI」的最低成本路径：调研四种契约流派里，生成式的三个样本后端都是自家 Python（`dev/dev/research/agent-frontend-survey.md:188`）。
3. SSE 与 WebSocket 都在 Starlette 里，换传输不需要换框架——这使 §5 的传输决策保持可逆。
4. 它是 Starlette 的薄封装，**没有自带 UI**，与「内核归自己」的取向一致（与 Chainlit 的关键差别）。

**代价与对冲**

- 依赖簇变大（对照 `pyproject.toml:7` 当前的单一 httpx）。对冲：FastAPI 进 `[project.optional-dependencies].web`，CLI 使用路径不装；`web/` 是唯一 importer，由 grep 门禁守着（`tests/test_web_boundaries.py`：内核侧不许出现 `fastapi` / `pydantic` / `starlette` / `uvicorn`，而 `src/avid` 里的 `fastapi` 必须全部落在 `web/`）。换框架（例如退到 Starlette 单用）只需改 `web/`，`svc/` 与内核不动——这正是 `web/` 这一层边界存在的理由。
- pydantic 与「内核类型是普通 dataclass」的取向冲突。对冲：内核类型（`Entry`、事件）保持 dataclass/`dict`，DTO 由 `web/schemas.py` 的显式 mapper 构造。**不允许让 `session/` 依赖 pydantic**——那会破坏 `session/__init__.py:10` 声明的「不 import 任何 avid 子包、只认识条目与 JSON」。

**反事实测试**（判据 §10）

| 条件 | FastAPI 方案是否成立 |
|---|---|
| 需求全变（改成纯 CLI 或 TUI） | 成立：内核不依赖它，删 `web/` 即可 |
| 规模 ×100（100 个并发会话） | 不成立：当前模型是「每会话一个线程 + 进程内注册表」，会话是文件后端且无跨进程锁（`runtime-architecture.md:426`）。此时需要队列与真正的会话锁，属于另一轮设计 |
| 依赖长期不可靠（浏览器→服务断连） | 成立：durable 事件可重放，UI 的权威视图来自 REST |
| 改成多用户/远程 | 不成立：需要鉴权、每用户工作区隔离、CSRF 面。当前 `.avid/` 目录就是权限边界 |
| 团队从 1 变 10 | 部分成立：契约与层禁规则让并行改动有边界，但 `web/schemas.py` 会成为合并热点 |

### 4.3 交付形态：分离开发、单进程交付

- **开发期两个进程**：`uv run avid web --port 8765`（只提供 API 与 SSE）+ `pnpm -C web dev`（Vite，代理 `/api` → `127.0.0.1:8765`）。Open WebUI 就是这套代理（`/api` 与 `/ws` 均 `ws: true`，`dev/research/agent-frontend-survey-final.md:427`）。
- **交付期一个进程**：`pnpm -C web build` → `scripts/copy-dist.mjs` 复制到 `src/avid/web/static/` → wheel 内含产物 → `avid web` 用 uvicorn 同时提供 API 与静态资源（SPA fallback）。**安装者不需要 Node**（Open WebUI 的发布者侧构建模型，`dev/research/agent-frontend-stack-survey.md:419`）。
- 明确不采用 Chainlit 式「打包/安装时现跑 pnpm」：缺 pnpm 时它的失败是构建期 `BuildError("pnpm not found!")`（`dev/research/agent-frontend-stack-survey.md:520-522`）。对一个全 uv 工具链的仓库，这是更差的失败模式。
- 产物漂移的对冲：`copy-dist.mjs` 写 `web/static/.build.json`（`git_sha` + `built_at`），`GET /api/meta` 返回它；从 checkout 构建时，`tests/test_web_boundaries.py` 断言构建戳的 `git_sha` 等于 `git rev-parse --short HEAD`（在 CI/本地构建路径上跑，安装路径跳过）。
- **路由规则**：`/api/*` 永远返回 JSON 或 SSE，**未知 `/api/*` 返回 JSON 404，绝不回落到 SPA 外壳**；其余路径静态资源 + `fallback=index.html`。这条取自 Langflow 的显式 catch-all（`dev/research/agent-frontend-stack-survey.md:435`）。

---

## 5. 传输与事件协议

### 5.1 决策：SSE + REST，不用 WebSocket

**解决了什么**：durable 事件需要「按游标补齐」，SSE 的 `id:` 字段与浏览器自动重发的 `Last-Event-ID` header 让这件事变成协议自带的能力；审批是请求/响应语义（有明确结果、必须幂等），用 REST 表达比在双向通道里约定控制帧更少状态。

**关键机制（不是偏好）**：

- **只有 durable 事件写 `id:`**。于是「哪些事件参与重连补齐」由编帧本身决定，不依赖任何约定；delta 不带 id，浏览器重连时 `Last-Event-ID` 自然停在最后一个 durable 点。
- 服务端为每个 run 保留有界重放缓冲（默认最近 512 条 durable 事件，可配置），`after` 落进缓冲之外时发一条 `resync` durable 事件，客户端据此重新拉取条目并重建视图——**不允许静默缺口**。
- 心跳：每 15s 一行 SSE 注释（`:ping`）穿透中间代理（Flowise 用 30s，`dev/research/agent-frontend-stack-survey.md:452`；此处取更保守值）。心跳与重连是独立模块，不依赖浏览器默认行为（n8n 的做法，`dev/research/agent-frontend-survey-final.md:423`）。

**牺牲了什么**：审批只能走 REST 往返（多一跳、多一份幂等要求）；同源 HTTP/1.1 连接数上限是浏览器级的——缓解办法是**只有前台会话保持一条事件流**（`visibilitychange` 时关闭后台流，重新可见时用游标恢复）。

**成立条件**：一个 run 至多一条事件流；浏览器（非原生客户端）；不需要服务端主动向非运行中的会话推数据。

**重新考虑的信号**：① 出现桌面壳或 IDE 插件需要同一份事件（届时 WS/IPC 复用同一事件类型即可，编帧层换 `web/sse.py`）；② 需要服务端向客户端推「非本 run 的变更」（例如另一个 run 改了同一个会话的条目）——那属于另一个通道，应单独设计而不是塞进 run 流；③ 实测连接数成为真实瓶颈（同一浏览器同时打开 >5 个运行中的会话）。

**被否掉的方案**：一条 WebSocket 承载全部帧。OpenHands 的新协议正是这个形状——7 帧判别联合（Sync/Durable/Transient/ItemStarted/Delta/ItemAborted/Error），投递规则写死在文件头（durable 可补齐、delta 可任意丢、每个 ItemStarted 必由 Durable 或 ItemAborted 关闭），背压是「丢连接不丢帧」（`dev/research/agent-frontend-stack-survey.md:180-184`）。它在**语义**上与本设计完全同构，差别只是承载；用 SSE 是因为当前不需要双向，而 WS 的重连与游标要自己实现。**一旦需要双向（例如流内提交下一条消息），直接照 OpenHands 的七帧形状升格，事件类型不变。**

### 5.2 事件分档

| 档 | 是否带 `id`/`seq` | 是否重放 | 成员 |
|---|---|---|---|
| durable | 是（run 内严格单调） | 是 | `run_started`、`user_message`、`assistant_message`、`tool_result_message`、`tool_call_started`、`tool_call_finished`、`tool_call_denied`、`approval_requested`、`approval_resolved`、`context_compacted`、`todo_reminder`、`stop_nudge`、`run_finished`、`run_failed`、`run_cancelled`、`resync` |
| transient | 否 | 否 | `run_status`（当前轮次、累计 token、当前活动工具） |
| delta | 否 | 否 | `assistant_delta`（正文增量）、`reasoning_delta`（推理增量） |

三档合起来 **19 个**（16 durable + 1 transient + 2 delta），与 `runtime/events.py` 的 `EVENT_TYPES`、`GET /api/meta` 的 `event_types` 是同一份——上表按档列全，不列"常见成员"，因为漏一个就会有人照着写错。其中 `todo_reminder` 只保留作 wire 兼容，阶段 29 起不再发射（TODO 计划改走 tail 块，不落库不发事件）。

四条规则：

- **delta 不落盘、不进会话、不重放。** 必须先决定这件事，因为它决定「重放语义」：若 delta 落盘，UI 投影就要处理「半截文本 + 完整文本」两种记录，且 `session/` 的条目不可变假设会被打破。代价是刷新后正在流式的消息会以「等待中」出现，随后的 durable 消息补齐——这是可接受的，因为 durable 消息带完整内容。
- **delta 默认不投递，消费方显式订阅。** 事件流默认只发 durable + transient；`assistant_delta` 需要订阅方显式声明（例如 `?deltas=1` 或首帧订阅消息）。这条抄的是 OpenHands 的做法：`receives_streaming_deltas: ClassVar[bool] = False`，注释写明 "consumers opt in rather than inherit them"（`dev/research/agent-frontend-survey-verified-addendum.md:414-417`）。收益是「不关心增量的消费者」不必承担合并与重排的成本。
- **不丢**：每个 durable 消息事件携带**完整**内容（`assistant_message.data.content` 就是最终文本）。因此 delta 全丢也不影响正确性。
- **单调**：一个 run 内 durable 事件的 `seq` 由唯一发射线程分配，严格递增、不重复。

**游标是 `seq`，不是时间戳**：断线补齐用 `Last-Event-ID`（就是最后一个 durable `seq`）。OpenHands 明确把旧的 `resend_mode` / `after_timestamp` 换成 `after_seq`，理由是时间戳比较的是各端的本地钟（`dev/research/agent-frontend-survey-verified-addendum.md:37`、`dev/research/agent-frontend-survey-supplement.md:145`）。因此 Avid 的事件里可以有 `ts` 用于显示，但**补齐只认 `seq`**；跨机时间只用于 UI 的相对时间显示，且以服务端时钟为准（LibreChat 的 `elapsedMs` 用服务端时钟规避跨机漂移，`dev/research/agent-frontend-impl-survey.md:274`；**该文件已于 2026-09-22 清理中删除，锚点不可复核**）。

### 5.3 事件类型与现有机制的映射

这张表是本设计的核心：**每条事件都能指到既有的一行代码**，新增的只有「谁把它发出来」。

| 事件 | 来源（既有机制） | 需要新增什么 |
|---|---|---|
| `run_started` | `svc.runs.start()` | svc 内生成 |
| `user_message` | `on_message` 的第一次 emit（UserPromptSubmit 注入**之后**，`loop.py:124`） | 无 |
| `assistant_message` | `on_message` 的 assistant 分支（`loop.py:164-165`） | 无 |
| `tool_result_message` | `on_message` 的 `role=tool` 分支（`loop.py:204-211`） | 无 |
| `tool_call_started` | `PreToolUse` hook context（`execution.py:70-76`）：`tool`/`arguments`/`round` | 注册一个转发回调 |
| `tool_call_finished` | `PostToolUse` hook context（`execution.py:90-98`）：`content`/`truncated` | 同上 |
| `tool_call_denied` | `PreToolUse` 返回 BLOCK 时的 `denied_kind`/`denied_reason`（`hooks.py:120,136`） | 同上 |
| `approval_requested` / `approval_resolved` | 注入的 `ask` 回调（§7.2） | svc 的 `approvals.py` |
| `context_compacted` | `context.announce()`（`context.py:48-56`）已有 `CompactReport` 与计数 | 让 observer 收一份 report |
| `todo_reminder` / `stop_nudge` | `loop.py:131-136` 与 `:189-194` 的注入点（文本分别是 `[提醒]` 前缀与 `nudge`） | 循环侧观察点（避免靠解析文本前缀区分） |
| `run_finished` | `agent_loop` 正常返回（`loop.py:199`） | svc |
| `run_failed` | `LLMError` / `ConfigError` / `SessionError`（内核异常映射） | svc |
| `run_cancelled` | 新增的取消路径（§7.4） | 内核 |
| `resync` | 游标落在缓冲之外 | `svc/runs.py` |

**映射到什么粒度**：`tool_call_started` 与 `tool_call_finished` 各自带 `tool_call_id`（来自 assistant 消息的 `tool_calls[].id`），因此 UI 能把三者（assistant 消息里的调用声明、开始、结束）缝在同一条卡片上。`entry_id` 由 `SessionRecorder` 返回（`recorder.py:45` 的 `append_message` 已返回 id），svc 在 emit 前把它带上——**前端不需要自己维护会话身份**。

### 5.4 时序：一次交互的完整往返

```
前端                        svc/web                      内核
 │ POST /sessions/{id}/runs ──▶ 注册表占位（占用→409）
 │ ◀── 201 {run_id}            起工作线程 ──────────────▶ agent_loop(ask=…, on_event=…)
 │ GET /runs/{id}/events ─────▶ SSE 订阅（after=0）
 │ ◀── event: run_started (id:1)
 │ ◀── event: user_message (id:2)      ◀── on_message(用户消息)
 │                                   ┌─ PreToolUse hook → tool_call_started (id:3)
 │                                   │  approval_requested (id:4)  ← ask 阻塞在工作线程
 │ POST .../approvals/{aid} ──▶ 置结果 + 唤醒线程
 │ ◀── event: approval_resolved (id:5)
 │                                   ├─ 执行工具（subagent 在池里并行）
 │ ◀── event: tool_call_finished (id:6)
 │ ◀── event: tool_result_message (id:7)  ◀── on_message(tool)
 │                                   └─ 下一轮 / Stop
 │ ◀── event: assistant_message (id:8)
 │ ◀── event: run_finished (id:9)
```

**刷新/重连的规则**（这是调研里最容易被做糊的一段）：

1. 页面加载：`GET /sessions/{id}/entries`（分页，从旧到新）渲染历史 —— **权威视图来自条目，不来自事件**。
2. `GET /sessions/{id}` 返回 `active_run_id`（若有）→ 打开 `GET /runs/{id}/events`，带 `Last-Event-ID: <已知最大 durable seq>` 或 `?after=`。
3. `after` 在缓冲内 → 补齐；在缓冲外 → 收到 `resync` → 回第 1 步重建。
4. **重放来的 durable 事件原子渲染，不做打字机效果**。AutoGPT 在这件事上的选择是「故意不做平滑」——重放整轮时慢速打字比一次跳变更糟（`dev/research/agent-frontend-stack-survey.md:502`）。
5. **delta 的收敛属正确性前提，不是性能优化。** 三个样本用三种写法表达同一条约束：LibreChat「`final` 前必须 `cancelPendingDeltaFlush()`，排队的 delta 绝不能落在服务端 final 写入之上」、OpenHands「任何非 delta 事件之前必须 `flush()`」、Cline「高 epoch 的单条 partial 只推进围栏、不清空，快照绝不截断」（`dev/research/agent-frontend-survey-verified-addendum.md:280-283`、`:263-278`）。Avid 的对应规则：**durable 事件渲染前 flush 待处理 delta；终止类事件（`run_finished`/`run_failed`/`run_cancelled`）渲染前 cancel 待处理 delta**。两种操作不混用——flush 是「把已有内容落上去」，cancel 是「别让旧内容盖住最终结果」。
6. **解析失败不静默丢弃**：SSE 行按 `\n\n` 分帧并保留半行缓冲（n8n 的 `data:` 行解析带半行 buffer，`dev/research/agent-frontend-stack-survey.md:485`）；一条帧的 `data` 解析失败时，**请求 `resync` 并标记该 run 为「视图待重建」**，不跳过。反面是 Dify：`JSON.parse` 失败即丢弃该行，分块边界切在 JSON 中间时该事件直接消失且无法察觉（`dev/research/agent-frontend-survey-final.md:414`）。
7. **权威终止不在流里**：`run_finished` 只是提示，权威事实是**运行注册表状态 + 已提交的条目**。OpenHands 在这件事上自陈有竞态：WS 的 `FINISHED` 帧不可作为唯一依据，必须回落到全量状态快照，并设 `TERMINAL_HARD_FALLBACK_SECS = 30.0` 兜底（`dev/research/agent-frontend-survey-verified-addendum.md:442-443`）。Avid 的对应实现：事件流静默超过 30s 或流结束时，客户端 `GET /runs/{run_id}` + `GET /sessions/{id}/entries` 对账；若注册表说已结束而前端没收到终止事件，就按持久层重建视图并提示「事件流不完整，已重建」。

**取消**：`POST /runs/{id}/cancel` → 标记 → 内核在**下一个检查点**停止（§7.4）→ 已产生的消息照常已落库 → `run_cancelled` durable 事件 → 前端把状态改为「已取消」而不是「失败」（取消不是错误：错误层要静默处理 abort，只有真正需要用户行动的失败才走可见路径，`dev/research/agent-frontend-survey-addendum.md:203`；**该文件已于 2026-09-22 清理中删除，锚点不可复核**）。取消**不**由客户端断开连接触发：与 Langflow 故意用 `Connection: close` + 0.1s 轮询做「关页面即停」相反（`dev/research/agent-frontend-survey-addendum-verified.md:166-168`），Avid 的 run 是**会持久化的**，关掉页面或刷新不能算取消；取消必须是一次显式命令。代价是「关了页面 run 还在跑」，对冲是运行状态始终可查（`GET /runs/{id}`）与 `run_status` 的累计 token。

**降级**：事件流不可用（代理不支持、连接被反复中断）时，前端降级为轮询 `GET /runs/{id}` + 条目增量，UI 顶部显示「实时通道不可用，正在轮询」。Langflow 的 `STREAMING/DIRECT/POLLING` 三档自动降级是现成形态（`dev/research/agent-frontend-survey-addendum-verified.md:166-168`）。代价写清：轮询下首 token 延迟与请求量都变差，因此轮询间隔是 1s 起步并随运行时长退避；这是**降级路径**，不是默认路径。

**审批**（含调研反复出现的两个坑）：

- 请求：`approval_requested {approval_id, tool, arguments, reason, kind, expires_at}`；`kind` 区分硬拒绝（不会变成请求，直接是 `tool_call_denied`）与规则审批。
- 答复：`POST /runs/{run_id}/approvals/{approval_id}` `{decision:"allow"|"deny"}`；服务端记住已决 id，重复投递返回 `200 {accepted:false, already:"allow"}` 而**不二次批准**——Cline 需要 `processedAskRef` + `askIdentity` 专门防这件事（`dev/research/agent-frontend-stack-survey.md:268`），Avid 把它做在服务端，UI 再做一次幂等展示。
- 失败关闭：超时（默认 120s，且必须小于 subagent 批次预算 300s，`tools/subagent.py:37`）、取消、连接断开、服务重启，全部收敛到 `deny`（`approval_resolved` 带 `reason`）。这条把既有的「失败关闭」约定（`hooks.py:19-20`、`permission.py:87`）延伸到网络边界。
- 恢复：`GET /runs/{id}/approvals` 返回当前待决项，供刷新后与第二个标签页查询；SSE 重放也会重发 `approval_requested`。
- 并发：subagent 最多 4 个并行（`tools/subagent.py:39`），因此**同一 run 内可能同时存在多个待决审批**；UI 按队列呈现并显示剩余数量，不再有「终端只有一个」的假设（`permission.py:60` 的 `_ASK_LOCK` 只为 CLI 路径保留）。

### 5.5 明确不做

| 不做 | 理由 | 重新考虑的信号 |
|---|---|---|
| subagent 内部事件转发（子 run 的消息/工具事件推到父流） | n8n 用 `ForwardedChildChunkWire` 做过（`dev/research/agent-frontend-survey-final.md:195`），但那要求父 run 的事件语义容纳子 run 的身份与嵌套；v1 的 UI 只需要「哪个子任务在做什么」 | 用户反馈「并行派发时看不到进度」≥2 次，或 subagent 单批耗时成为常见等待 |
| 会话树的分支/fork 视图 | `session/` 已有 branch 与 value（`values.py:19-21`），但当前 CLI 只用一个 `main` 分支；Onyx 的消息树（`Map<nodeId, Message>` + `childrenNodeIds`，`dev/research/agent-frontend-stack-survey.md:540`）与 Avid 条目树同构，是明确的后续形态 | 出现第一次真实的 fork/重新生成需求 |
| 前端侧 Markdown 增量渲染库（streamdown 类） | Dify 与 AutoGPT 都用它（`dev/research/agent-frontend-survey-final.md:221`），但它是 React 生态绑定且体积不小；v1 的消息是整条到达 | 接入 delta 后，若自研渲染的帧率不达标 |
| 前端文件写入 / 目录浏览 API | 本地反面样本 purrcat 的人类面板绕过权限模型直接写文件（`ui/src/components/chat/IDEPanel.tsx:411-415` 经 Electron `fs:writeFile`，或 `POST /api/filesystem/write` 只做路径转换 + `open().write`），而 agent 侧有 `require_write` 闸门——同一个仓库两套写路径 | 需要人类手动改文件时，**经工具管线**暴露（走 `write_file` 与审批闸门），而不是新开一条 API |
| 桌面壳（Electron/Tauri） | OpenHands 的打包链要同时带 uv 与 Node 分发（约 130 MB），并用 `afterPack` 把误拷的 `node_modules` 换成 7 MB 闭包（`dev/research/agent-frontend-survey-final.md:498`）。参照实现 purrcat 有完整的 Electron 壳可抄（`electron/main.js` 的 sidecar spawn + 看门狗 + 就绪轮询 + 超时错误页、`preload.js` 的窗口与 `fs:*` 桥），**但它的 `fs:readFile/writeFile/readDir/stat` 没有路径白名单，照抄前必须先加** | 需要在没有终端的机器上分发时 |
| 多用户 / 鉴权 / CSRF | 当前是单机单用户，`.avid/` 目录即边界 | 服务监听非 loopback，或出现第二个使用者 |

---

## 6. 前后端接口约定

### 6.1 端点表（v1）

| 方法 | 路径 | 语义 | 成功 | 主要错误 |
|---|---|---|---|---|
| GET | `/api/meta` | 版本、**特性表**、能力面（工具名、技能目录、模型名）、构建戳 | 200 | — |
| GET | `/api/sessions` | 会话列表（元信息 + 条数） | 200 | — |
| POST | `/api/sessions` | 新建会话（可指定 id/name） | 201 | 409 已存在 |
| GET | `/api/sessions/{id}` | 元信息 + 统计 + `active_run_id` | 200 | 404 |
| PATCH | `/api/sessions/{id}` | 改名（= 值写入） | 200 | 404 |
| DELETE | `/api/sessions/{id}` | 销毁（要求无活动 run） | 204 | 404 / 409 |
| GET | `/api/sessions/{id}/entries` | 条目分页：`branch`/`order`/`limit`/`cursor_seq` | 200 | 404 |
| POST | `/api/sessions/{id}/runs` | 起一次运行：`{prompt, auto_approve?}` | 201 `{run_id}` | 409 已有活动 run |
| GET | `/api/runs/{run_id}` | 运行状态与统计 | 200 | 404 |
| GET | `/api/runs/{run_id}/events` | **SSE**，支持 `Last-Event-ID` 与 `?after=` | 200 `text/event-stream` | 404 |
| POST | `/api/runs/{run_id}/cancel` | 请求取消 | 202 | 404 / 409 已结束 |
| GET | `/api/runs/{run_id}/approvals` | 待决审批列表 | 200 | 404 |
| POST | `/api/runs/{run_id}/approvals/{aid}` | 答复：`{decision}` | 200 `{accepted}` | 404 / 409 已决 / 410 已过期 |
| GET | `/api/skills` | 技能目录（name + 一行描述，与 system prompt 同源） | 200 | — |
| GET | `/api/health` | 就绪探针（供桌面壳/脚本） | 200 | — |

**分页纪律（服务端，不是前端）**：条目列表**必须**有默认 `limit`（100）与硬上限（500），游标用 `cursor_seq` 反向分页并以 `(seq)` 打破并列——`session/types.py:118-141` 的 `EntryQuery`/`BranchScan` 已经有 `limit`/`cursor_seq`，不需要新机制。这条不是风格问题：Langflow 在这个点上出过一次有数字的事故——监控端点缺省返回全量历史，「19k messages 每次请求约 34 MB」，编辑器每 5 秒轮询导致界面冻结；修复方式是默认 `limit=100` + 硬上限 + 反向分页 + 复合索引（`dev/research/agent-frontend-survey-addendum-verified.md:165`）。**长历史靠服务端分页解决，不是靠前端虚拟化。**

**一个被 Web 首屏放大的既有代价**：`GET /api/sessions` 要显示会话名与条数，而名字是会话文件里的一个值、条数要读全部条目，所以这个端点当前是 O(会话数 × 文件大小)——`cli.py:13-15` 已经承认了这个代价（`--list-sessions` 的 `_peek` 会打开每个会话，`cli.py:238-247`）。CLI 下它是「敲一次命令等一会」，Web 下它是**每次刷新首屏**。处置：v1 接受这个代价；当会话数使首屏明显变慢时，把会话名冗余进 JSONL header（`cli.py:13-15` 已写明这条路径）。

**写权限的归属**（判据 §4/§6）：会话条目的写入者有且只有 `SessionRecorder`。因此 API 里**没有**「追加条目」端点——前端不是这些对象的作者。人类要写文件时，走 `/api/runs` 让 agent 去调用对应工具，权限闸门与审计因此不被绕过。同理，服务端**不把「是否执行」的判定委托给浏览器的可用性**：浏览器只是决策的输入端，未答复一律收敛为拒绝（超时、取消、断连、重启四条路径都收敛到 `deny`，见 §5.4）——这与 Open WebUI 的反向 RPC（后端 `sio.call` 阻塞等浏览器回包才决定是否执行，`dev/research/agent-frontend-survey-addendum-verified.md:196`）是相反取向，那样会把权限判定拆到两个信任域。

### 6.2 载荷与错误模型

- **DTO 由显式 mapper 从内核 dataclass 构造**（`web/schemas.py`），字段名与内核一致，不做驼峰转换以外的重命名；JSONL 里已经是 camelCase 的字段（`parentId`/`storageVersion`，`session/types.py:4-5`）在 API 里保持同形，避免「同一概念两种拼写」。
- **事件线格式**（SSE）：

```
id: 42
event: tool_call_finished
data: {"run_id":"run_...","session_id":"s_...","seq":42,"ts":1773...,
       "type":"tool_call_finished",
       "data":{"tool":"bash","tool_call_id":"call_1","entry_id":"e_...",
               "status":"ok","truncated":false,"duration_ms":412,
               "content_chars":1830}}

event: assistant_delta
data: {"run_id":"run_...","seq":null,"text":"…"}      ← 无 id 行，不参与重连
```

- **错误信封**：所有非 2xx 返回 `{"error":{"code":"run_busy","message":"…","detail":{…}}}`，`code` 是稳定字符串，与事件类型共用一份命名规则。
- **业务失败不是 HTTP 错误**：工具失败按项目既有约定回文本、不抛异常（`execution.py`、`runtime-architecture.md` §8.2 D2），因此它以 `tool_call_finished` 事件出现，`data.status` ∈ `ok | denied | failed | truncated`。`status` 的来源：`truncated` 与 `denied_kind` 直接来自 hook context；`failed` 只能由内容前缀判定。**前缀自 2026-09-19 起分三类、各有唯一出处**：`错误：`（业务拒绝，各工具自己回）、`参数错误：`（参数不合 schema，`tools/validate.py::bad_arguments`）、`执行失败：`（程序 / 环境错误，`execution.py`）；判定仍是单点（`web/schemas.py` 的 `_FAILED_PREFIXES` + `_FAILED_TOOL_MARK`，受测）。
  本条原记的**重新考虑信号**（「出现第二条错误文本约定」）已经发生——参数错误就是第二条。**这次仍不换成 `ToolOutcome.status`**，理由：① 事件载荷是 durable 的，加字段意味着历史会话缺字段、要么迁移要么回落，而当前收益只是「判定更稳」；② 三类措辞现在各只有一处出处，漂移面已经很小。**新的重新考虑信号**：出现第二处按失败种类分流的消费者（评测按失败类型统计、循环按可重试性决定收尾），或前缀判定出现一次误判；届时的落地路径是把 `status` 加进 `ToolOutcome` 与 `tool_call_finished`，`classify_tool_status` 优先读它、缺失时回落前缀以兼容旧日志。
- **HTTP 错误码只承担传输与生命周期语义**：400 参数、404 未知 id、409 状态冲突（会话已有活动 run / 审批已决）、410 审批过期、422 schema、500 内部、503 模型不可达。

### 6.3 版本与漂移门禁

- `GET /api/meta` 返回 `api_version`（整数，破坏性变更时 +1）、`event_types`（完整清单）与 **`features`（特性表）**。
- **按特性分支，不按版本号分支**：客户端读 `features`（例如 `{"deltas":1,"branches":1,"usage":1}`）决定启用哪些能力，只在客户端构建的 `api_version` 与内核声明**不兼容**时才失败收敛。形态取自 OpenHands 的两层防护——构建期钉死版本 + 运行期按特性协商（`AGENT_SERVER_VERSION_TOO_OLD` + feature→minVersion 表 + `/server_info` 缓存，`dev/research/agent-frontend-survey-verified-addendum.md:418-423`）。特性表比单一版本号更耐漂移：加一个可选事件不会让所有旧前端罢工。
- 失败收敛的具体表现：不尽力渲染，而是显式报出「界面与内核版本不兼容」并要求重新构建 `web/`（LibreChat 的协议协商就是这个形状，`dev/research/agent-frontend-impl-survey.md:274`；**该文件已于 2026-09-22 清理中删除，锚点不可复核**）。
- **机械检查（先做这个，不上生成器）**：`tests/test_event_contract.py` 解析 `web/src/events/types.ts` 的联合类型成员集合，与 `runtime/events.py` 的 `EVENT_TYPES` 比较集合相等。理由：生成式契约不是免费的——Dify 生成前要打 6 类规范化补丁、OpenHands 要维护公开面过滤 + 人工 `allowClientOnly` 清单并已出现生成源 1.47.0 与运行时 1.49.1 的静默漂移（`dev/research/agent-frontend-survey-final.md:41`）。在「事件数量 × 变更频率」超过人工同步成本之前，一条集合相等测试比一套生成器便宜（这条判据取自 `dev/research/agent-frontend-survey-final.md:478`）。
- **升级到生成器的条件与路径**（写清以便将来照做）：事件类型 ≥ 25 个，或单次迭代要改 ≥ 3 个事件的载荷结构时，改用 FastAPI 的 OpenAPI 做**类型生成**（hey-api，只生成类型不生成方法体，OpenHands 的形态），门禁用 Dify 的「CI 先删再生成再 diff」（`dev/research/agent-frontend-survey-final.md:118`）。
- **破坏性变更的跑道**（生成器时代才需要，现在记录以免将来临时发明）：OpenHands 的做法是 5 个 minor 版本的弃用跑道 + 用 `oasdiff` 对比上一个 PyPI 发布 + CI 校验弃用话术；弱 schema 的允许清单必须带 `reason` / `owner` / `expiry` / `follow_up` 四个字段（`dev/research/agent-frontend-survey-verified-addendum.md:435-439`）。Avid 当前的规模不需要它，但**契约一旦开始生成，废弃就必须有到期日**，否则抽象会永久滞留。

---

## 7. 内核为浏览器提供的接口

七处，**只有第 4 项碰调度**；这些改动已在内核落地，下面写的是它们向浏览器提供了什么。

### 7.1 `runtime/events.py`：事件类型单点 + 观察者

```python
EventType = Literal["run_started", "user_message", ..., "resync"]
EVENT_TYPES: tuple[str, ...] = (...)          # 单点
@dataclass(frozen=True)
class RunEvent:                                # 普通 dataclass，不是 pydantic
    type: str; run_id: str; seq: int | None; ts: int; data: dict
class RunObserver(Protocol):
    def __call__(self, event: RunEvent) -> None: ...
```

`agent_loop(..., on_event: RunObserver | None = None)`：`on_event` 只承载**循环自己才知道**的事实（轮次开始、TODO 提醒、Stop nudge、结束/取消），其余全部来自既有 hook。**这不是「第二个观察点」**：`on_message` 仍是消息的唯一通道，`on_event` 是步骤级事实的通道；两者都不改调度。代价：事件类型公开后即承担兼容责任；这一层本身就能在**不启动 UI** 的情况下订阅并断言，UI 只是第二个消费者（顺序取自 `dev/research/agent-frontend-survey-final.md:472`）。

### 7.2 审批注入

这一处要解决的问题：`permission_hook` → `check_permission(name, arguments)`（`hooks.py:131`）→ 默认 `ask_user`（`permission.py:128`）→ `sys.stdin.readline()`（`permission.py:101`）。**在由 uvicorn 启动的进程里，stdin 不是终端**，这条路径会读到 EOF（直接拒绝）或阻塞；而且 `_ASK_LOCK`（`permission.py:60`）是模块级全局锁，会跨会话互相阻塞。

实现（4 处，都是把已有参数接上）：

1. `RunState.ask: AskUser | None = None`（`state.py:24-45`），`for_run(auto_approve=…, ask=…)`。
2. `agent_loop(..., ask: AskUser | None = None)`（`loop.py:83-97`）→ 传入 `RunState`。
3. `execution.execute_one` 把 `state.ask` 放进 `PreToolUse` 的 context（`execution.py:70-75`）。
4. `permission_hook` 优先用 `context.get("ask")`，没有则回落到 `ask_user`（CLI 行为逐字不变）。

加上 `subagent.py` 的前传（`run_subagent(..., ask=None)` 与 `subagent()` 从 `state.ask` 取值，各 2 行）：否则子 agent 的审批会落到 stdin 上——这是**当时就存在的隐含缺陷**，只是 CLI 下被 `_ASK_LOCK` 与共享终端掩盖了。

`AskUser` 的签名保持 `(name, arguments, reason) -> bool`（`permission.py:54`），不改类型：Web 侧要在「允许一次 / 本 run 内对同一工具总是允许 / 拒绝」之间选择，做法是**注入的 ask 回调内部维护 run 作用域白名单**，命中时直接返回 `True` 而不发起请求。这条把「策略」放进了协议适配层；依据是 `ask` 参数本就是策略注入点（`permission.py:112`），删除测试：删掉这个白名单，用户每次都要重新批准（体验变差但不破坏边界）。**准入证据**：出现第二个需要同样语义的调用方（例如桌面壳）时，才把它上移到 `policy/permission.py`。

### 7.3 压缩事件

`context.announce()` 已经有 `CompactReport` 与计数（`context.py:48-56`），它把 report 交给 observer（`announce` 多一个可选参数）。**为什么内核要发这条事件**：gemini-cli 把 `chat_compressed` / `context_window_will_overflow` 这类上下文治理事件放进了它 18 个事件的枚举（`dev/research/agent-frontend-survey-final.md:408`）——压缩、循环检测、权限阻塞这些内核行为必须可被观察，否则消费方只能把它们渲染成没有解释的等待。

### 7.4 取消（唯一碰调度的一处）

`RunState.cancelled: bool` + `RunState.cancel_reason: str | None`；`loop.py` 在**两个位置**检查：每轮开始前与每批工具执行前。命中则抛出 `RunCancelled`（终止原因，不是错误），svc 捕获后发 `run_cancelled`。取消是**唯一**的提前结束路径——循环没有轮数上限，也没有别的"未完成"终止原因。

- **粒度**：一个步骤。取消发生在「在飞的模型调用返回后」或「在飞的工具调用结束后」；最坏等待是一次模型调用（`TIMEOUT_SECONDS = 60.0`，`client.py:16`）。这个粒度是接口语义的一部分：不能承诺立即停止。
- **不丢已产生的消息**：消息在产生时就经 `on_message` 落库（`recorder.py:42-47`），所以取消不需要补偿写。
- **被否掉的方案**：用注入的 `chat` 包装器（`loop.py:90` 允许）在每轮前抛异常 + 用 `PreToolUse` 在取消时 BLOCK。理由：hook 抛异常按 BLOCK 处理（`hooks.py:83-86`），于是取消会在 transcript 里塞进一条「被拒绝的工具调用」消息并被提交，而那条消息对模型与后续续接都是噪音；而且取消状态会散在两个适配器里。**代价**：这是全文唯一为前端改调度的地方，所以两个检查点各自要有测试钉住「取消不丢已产生的消息、也不产生伪造的工具结果」。

### 7.5 流式模型调用

`ai/client.py` 的 `stream_completion(config, messages, *, system, tools, max_tokens, on_delta, client=None) -> Turn`：用 `httpx.Client.stream` 解析 `stream: true` 的 `data:` 行，累加 `content` 分片与 `tool_calls` 分片（按 `index` 归并），最后返回与 `parse_turn` **同形**的 `Turn`。

- **不需要动循环**：`chat` 是注入参数（`loop.py:90`），svc 传入一个绑定了 per-run 回调的 wrapper 即可。
- **难点写清**：`tool_calls` 的参数是分片流式到达的，必须按 index 拼接后再 `json.loads`；这是本项目里最容易写错的一段，所以累加器要做成纯函数并配 fixture 测试。
- **验收**：同一段 mock SSE 与同一份非流式 JSON 必须产出逐字段相等的 `Turn`（`tests/test_llm.py` 的流式等价用例）。
- **流式不是正确性的前提**：durable 的 `assistant_message` 始终携带完整内容，delta 全丢也不影响最终视图。

### 7.6 会话「上次运行中断」的可见性（靠投影派生）

服务重启会杀掉 run；客户端发现 `GET /runs/{id}` 404 且会话条目链尾是一批没有结果的 `tool_calls`。`session/projection.py:41-72` 的 `repair_incomplete_batches` 本来就会把这批丢掉以保证续接合法。接口不需要新字段：条目 API 附带 `truncated_tail: true`（由同一次投影计算得出），据此可以在链尾提示「上次运行在此中断」。**不新增持久化字段**，因为这件事是**派生**的。

### 7.7 不提供人类侧的任务写入端点（已随任务图下线，阶段 27）

没有人类侧的任务写入端点（§6.1）。理由与反例见 §5.5。

阶段 27 把任务图整体下线——六个任务工具、`tools/tasks.py`、`svc/tasks.py`、
`web/routes/tasks.py`、`GET /api/tasks{,/{id}}` 与 `/tasks` 页面一并删除，本节记的这条非目标
因此没有了对象。保留它只为留下两件事：当时为什么不给人类写路径（写入者是 agent，加一条写
路径就要重新论证它是否绕过存储层校验），以及"这次对话的计划"现在由 `todo_write` 工具承接
（`runtime-architecture.md` §17 有下线说明）。旧的 `<工作区根>/.tasks/` 数据不
迁移、不删除，只是不再被读。

---

> **§8–§17 已于阶段 32 随旧前端一并删除。** §8–§17：视觉语言（暖羊皮纸 + 液态玻璃）、性能与体积、失败与恢复、不变量、并发与一致性、判据覆盖、验收标准、实施顺序、适用条件与失效信号、未验证假设——描述的是已被删除的那份前端的内部设计、门禁与验收。
