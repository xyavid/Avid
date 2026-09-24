# Avid 前端架构设计

**状态**：设计，未实施（本文写作时仓库代码零改动）。
**依据**：按 `docs/design/architecture-criteria.md` 的 12 组检查点逐条推导；每条取舍写「解决了什么 / 牺牲了什么 / 在什么条件下成立 / 什么信号出现时重新考虑」。
**证据来源**：仓库内文件用 `path:line`；调研结论用 `dev/research/*.md:line`（过程文档，不入库，引用时同时写明样本与 commit）。未取得证据的一律标 **未验证假设**。2026-09-22 清理冗余调研产物时删除了 `agent-frontend-survey-addendum.md` 与 `agent-frontend-impl-survey.md` 两份并发稿，上文 4 处引用它们的锚点已就地标注为不可复核；合并定稿 `agent-frontend-survey-final.md` 与详情分册 `agent-frontend-survey-deepdive.md` 保留。
**前置阅读**：`docs/design/runtime-architecture.md`（内核四层与不变量 I1–I7）、`dev/plan/roadmap.md`（已完成阶段）。
**产出图**：`dev/architecture/phase-14-frontend.svg`（过程文档，只存本地）。
**视觉方向**：涂鸦潦草风，参照 `dev/tmp/purrcat-src/ui/` 的实现（见 §8）。

---

## 0. 结论速览

| # | 决策 | 一句话理由 | 主要代价 |
|---|---|---|---|
| D1 | 后端 **FastAPI + uvicorn**，只住在 `src/avid/web/` | 调研内 5 个 Python 后端 agent 全部是 FastAPI/Starlette 系，且能白得 OpenAPI 这一份单一契约源 | 内核首次引入非 httpx 依赖簇；靠「内核不 import 框架」的 grep 门禁把代价关进适配层 |
| D2 | 传输 **SSE（每次运行一条事件流）+ REST（命令与查询）**，不用 WebSocket | durable 事件需要按游标补齐，SSE 的 `Last-Event-ID` 让「只给 durable 事件发 id」直接等于「重连从最后一个 durable 点续」 | 审批只能走 REST 往返；HTTP/1.1 下同源连接数受浏览器限制 |
| D3 | 事件**分三档**：durable（带 `id`/`seq`，可重放）/ transient（状态，不重放）/ delta（无 `id`，可任意丢） | 调研里重连语义说不清的样本（Chainlit、n8n）都是把三类压进同一通道且不分档 | 前端要多实现一层合并器与重放处理 |
| D4 | **UI 之前先补事件层**：`runtime/events.py` + 观察点，不动调度 | Chainlit 的 `on_message` 与 Avid 循环的观察点同构，但它有三类显式 UI 事件而 Avid 只有整条消息级 | 事件类型一旦公开就要承担兼容责任 |
| D5 | 内核只加 **7 处小改动**（§7），其中只有取消检查碰调度 | 审批与事件都能借既有的 `ask` 参数与 hook 机制完成 | `agent_loop` 多两个关键字参数 |
| D6 | 状态分三域：**REST 权威域（TanStack Query）+ 活动域（zustand + 纯 reducer）+ 界面域（zustand + localStorage）** | 调研里混用事件与查询缓存的样本付出了手写失效图的代价 | 两套状态机制并存，要在 lint 层禁止越界 |
| D7 | 前端 `web/` 与 Python 包**分离开发、单进程交付**：产物复制进 `src/avid/web/static/` 随 wheel 分发 | 安装者用 uv，不该需要 Node；Chainlit 式「装包时现跑 pnpm」的失败模式更差 | 产物可能与源码不一致，需要构建戳 + 一致性检查 |
| D8 | 契约 = **Python 侧事件/条目类型为单点**，OpenAPI 生成 TS 类型，外加一条「两侧清单一致」的机械检查 | 生成器这条路要等「事件数量 × 变更频率」超过人工同步成本才划算 | 早期多一条测试，少一次代码生成 |
| D9 | 视觉方向 = **涂鸦潦草风，照 purrcat 实现并 token 化**（**已被阶段 23b 替换为暖羊皮纸 + 液态玻璃，见 §8**）；落地靠 token 单点 + 会失败的样式禁令 + a11y 阻塞门禁 | 语言在 purrcat 里已经成形，但 738 处 hex / 498 处硬阴影 / 228 处内联手写字体全部未 token 化——这是「真实重复」的教科书案例 | 初期写组件的摩擦变大；偏离 token 的临时样式会被拦 |
| D10 | v1 **不做**：首页、Web 终端、IDE 面板、桌面壳、多用户/鉴权、中文手写体、i18n 框架、subagent 子事件转发、虚拟列表 | 每一条都有明确的重新考虑信号（§5.5、§16） | 形态朴素 |

**当前基线（写作时实测）**：内核运行期依赖只有 `httpx>=0.27`（`pyproject.toml:7`）；参照实现 purrcat 的 UI 为 React 18 + Vite 5 + Tailwind 3，视觉语言已成形但未 token 化（738 hex / 498 硬阴影 / 295 墨边 / 228 内联手写字体，§8.0）；`agent_loop` 的对外观察点只有 `on_message`（`src/avid/runtime/loop.py:96,103`）；模型调用非流式（`src/avid/ai/client.py:166-178`）；本机 Node `v24.15.0`、pnpm `10.29.3`、uv `0.12.9`；**CI 已存在**（`.github/workflows/ci.yml`，三个 job：内核 ruff/mypy/pytest、stress 门禁、前端 install→build→`verify`）——本节其余数字仍是写作时的实测，CI 这一条已按现状更新。

---

## 1. 变化优先：先定位会变的东西

### 1.1 前端侧的变化清单

| 变化 | 频率 | 时间窗 | 代价 | 现在被谁绑住 |
|---|---|---|---|---|
| 事件类型增删（新工具、新压缩步骤、新审批语义） | 高 | 持续 | 中：前后端两侧类型 + 渲染分支 | 尚无事件层，改动会直接落在循环与 UI 两处 |
| 时间线里的条目渲染（新工具结果形态、diff、终端输出） | 高 | 持续 | 低：单个 pattern 组件 | 尚无组件层 |
| 传输与协议（先 SSE，可能要 WS / 桌面壳 IPC） | 中 | 数周–数月 | 高：重写连接与重连逻辑 | 尚未选型；本文把它收进 `web/` 与 `src/api/stream.ts` 两个文件 |
| UI 视觉（布局、密度、配色） | 中 | 持续 | 低：token 与 pattern 组件 | 尚无 token |
| 模型调用是否流式（现状非流式） | 中 | 数月 | 中：`ai/client.py` 增一条解析路径 | `chat` 已是可注入参数（`loop.py:90`），不需要动循环 |
| 会话形态（分支、fork、压缩条目） | 中 | 数月 | 中：条目树视图 | `session/` 已有 branch/value（`src/avid/session/values.py:19-21`），前端未见 |
| 部署形态（CLI → 本地服务 → 桌面 → 嵌入他人页面） | 低 | 数月–年 | 高：样式作用域、鉴权、打包 | 尚未发生 |
| 多用户 / 鉴权 | 低 | 未定 | 高 | 当前单用户单机，`.avid/` 就是权限边界 |

**优先级**（按「频率 × 代价」）：事件类型 > 时间线渲染 > 传输形态 > 视觉 > 会话形态 > 部署形态。因此本设计的重心是**事件层与契约**，视觉与性能排在它们之后，但都给出可执行门禁。

### 1.2 现状的量化证据（为什么「先补事件层」不是偏好）

- 循环只有一个观察点，且只发**整条消息**：`on_message` 在 `loop.py:112-114` 被调用 5 处（触发用户消息、TODO 提醒、assistant、Stop nudge、每条工具结果），签名是 `Callable[[dict], None]`（`loop.py:96`）。没有「本轮开始」「工具开始」「压缩发生」「审批待决」这些**步骤级**事实。
- 工具的执行前事实只存在于 hook 的临时 `context` 里：`{"tool","arguments","round","auto_approve"}`（`execution.py:70-75`），执行后是 `{"content","truncated"}`（`:90-96`）。这些字段**没有任何消费者会持久化或转发**——`log_hook` 只写日志（`hooks.py:145-158`）。
- 压缩发生时有日志与计数，但没有事件：`context.announce()` 只做 `logger.info` + `state.compactions += 1`（`context.py:48-56`）。
- 模型调用一次性返回，没有增量文本：`chat_completion()` 用 `httpx.Client.post` 取整个 JSON（`client.py:140-178`），`Turn.text` 是完整字符串（`client.py:125-132`）。
- 审批是**进程内阻塞读 stdin**：`permission_hook` 调 `check_permission(name, arguments)`（`hooks.py:131`），后者默认 `ask = ask_user`（`permission.py:128`），`ask_user` 读 `sys.stdin.readline()`（`permission.py:101`），并用模块级 `_ASK_LOCK` 串行化（`permission.py:60,93`）。

**推论**：在没有 UI 的情况下，这四处缺口每一条都已经是可测试性缺陷（没法断言「工具开始过」、没法断言「压缩发生过」、没法断言「审批被谁拒绝」）。所以事件层不是为了 UI 才做的，UI 只是它的第二个消费者。这条与调研最强的结论一致：**加 UI 的前置不是选前端框架，而是先把事件层补成三类可观察点**（`dev/research/agent-frontend-survey-final.md:47`）。

### 1.3 由此得到的设计优先级

1. 事件类型与观察点（内核，可无 UI 验收）。
2. 传输与契约（SSE 分档 + OpenAPI + 两侧清单检查）。
3. 状态三域切分（禁止把事件写进查询缓存）。
4. 组件分层与 token（美观性的可执行部分）。
5. 性能门禁（体积、首屏、首 token、长会话）。

---

## 2. 具体先行：抽象准入与删除测试

引入每层抽象前必须满足「真实重复 / 真实变化 / 真实耦合」之一并指名；删除测试不过则撤回。

| 抽象 | 准入证据 | 删除测试（删掉会怎样） | 判定 |
|---|---|---|---|
| `svc/`（应用服务，与 `cli.py` 同级） | **真实重复**：`cli.py` 已经在做「取历史 → 建 recorder → 跑循环 → 落库」的编排（`cli.py:149-198`）；前端要同一编排 | 删掉则 Web 路由必须自己写这段编排，于是产生第二份接线，`agent_loop` 的调用点从 2 个变 3 个 | **保留** |
| `web/`（传输适配） | **真实变化**：调研记录四种交付形态（独立 Web / 随包分发 / 宿主 webview / 终端），协议各不相同 | 删掉则 `svc/` 必须直接生成 HTML/SSE，业务编排与传输形态重新耦合 | **保留**，但只准 `web/` import FastAPI（§14 A2） |
| `runtime/events.py`（事件类型单点） | **真实重复**：同一批事实要被三处消费——日志、会话落库、UI | 删掉则事件名会同时出现在 hooks、loop、web、前端四份清单里（这正是 Chainlit 的状态：事件名两侧各写一遍且无漂移校验，`dev/research/agent-frontend-survey-final.md:435`） | **保留** |
| 前端 `ui/` 设计系统层（涂鸦 token + 原语 + pattern） | **真实变化**：主题、密度、嵌入宿主都只影响这一层；且**真实重复**已量化——purrcat 的涂鸦语言被写成 738 处 hex、498 处硬阴影、41 处内联圆角、228 处内联手写字体（§8.0） | 删掉则每个 feature 自己写颜色、按钮与歪斜卡片，重复从一处扩散到十几处；参照实现就是这个状态 | **保留** |
| 前端 `events/reducer.ts`（纯函数状态收敛） | **真实变化**：重放、重连、去重、取消都只改这一个纯函数 | 删掉则合并逻辑散进各组件，无法脱离 UI 单测（调研将「合并器是纯函数 + 调度器分离」列为 OpenHands 两处最值得抄的机制之一，`dev/research/agent-frontend-survey-final.md:486`） | **保留** |
| 前端虚拟列表 | **无证据**：条目长度当前不可预测，但没有 1k 条真实会话可测 | 删掉更简单；先做折叠 + 分组 + 分页，虚拟化等实测数字 | **不做**（信号见 §16） |
| 前端 i18n 框架 | **无证据**：单用户本地工具，无第二语言消费者 | 删掉更简单；文案集中到单文件即可 | **不做**（做法见 §8.6） |
| 任务板的写操作 API | **无证据**：任务的写入者有且只有 agent（当时是 `tools/tasks.py` 的六个工具） | 删掉更简单；且加一条人类写路径就要重新论证它是否绕过 `TaskStore` 的校验 | **不做**；阶段 27 把任务板连同任务图整体下线，这一行只作历史记录（§7.7） |

---

## 3. 架构总览

### 3.1 分层与职责边界

在既有四层（应用 / 运行时 / 策略 / 协议与能力，见 `docs/design/runtime-architecture.md:47`）之上垂直加两层，**不改既有层的边界**：

| 层 | 目录 | 职责 | 明确不做 | 依赖方向 |
|---|---|---|---|---|
| 传输适配 | `src/avid/web/` | HTTP 路由、DTO（pydantic）、SSE 编帧、静态资源与 SPA fallback、错误码映射 | 不知道 `agent_loop` 的调度细节；不直接写会话 | → `svc/` |
| 应用服务 | `src/avid/svc/` | 运行注册表与生命周期、事件缓冲与重放、审批待决表、会话读 | 不 import FastAPI；不做 `while`/按轮次循环 | → `runtime/`、`session/`、`tools/`、`policy/` |
| 应用（既有） | `src/avid/cli.py` | 终端接线：参数、stdin 审批、stdout 输出 | — | → `runtime/`、`session/` |
| 运行时（既有） | `src/avid/runtime/` | 调度、状态、压缩编排、工具执行、hook、**新增事件观察点** | 不认识 HTTP、不认识持久化 | → `ai/`、`tools/`（`policy/` 仅经 state 与 hook） |
| 策略（既有） | `src/avid/policy/` | 阈值、文案、权限规则、技能、压缩步骤 | 不认识 HTTP | → `ai/` |
| 会话（既有） | `src/avid/session/` | 条目树 / 值 / 分支 / 后端 / 投影 | 不 import 任何 avid 子包（`session/__init__.py:10`） | 无 |
| 协议与能力（既有） | `src/avid/ai/`、`src/avid/tools/` | 模型协议、工具协议与实现 | 不认识 HTTP | 无 |
| 前端 | `web/` | 全部浏览器代码 | 不复制内核判断（何时继续/何时问） | → `web/src/api/`（唯一的网络出口） |

**每个边界的依据**（判据 §4）：

- `web/` ↔ `svc/`：隔离「传输形态」这一高频变化。谁说了算——`svc/` 决定业务时序，`web/` 决定线格式。
- `svc/` ↔ 内核：隔离「多一个调用方」。不变量是**内核不知道有几个调用方**；`cli.py` 与 `svc/` 是它的两个使用者，二者都不许改内核的调度。
- 前端 ↔ 内核：隔离语言与部署单元。**契约是唯一的耦合面**，所以契约必须有单点定义和漂移检查（§6.3）。

### 3.2 三条数据流

1. **命令流**（前端 → 内核，低频、要成功或失败）：`POST /api/sessions/{id}/runs` → `svc.runs.start()` → 工作线程 `agent_loop`。
2. **查询流**（前端 → 内核，幂等、可缓存）：会话列表 / 条目分页 / 技能目录 / 运行状态。
3. **事件流**（内核 → 前端，单向、可分档丢失）：hooks + 循环观察点 → 事件 → 运行缓冲 → SSE。
   反向的**人机交互**不走事件流：审批答复是命令流（`POST .../approvals/{id}`）。

三条流的所有权：命令与查询的权威在 `session/`（条目）；活动运行的**权威也在 `session/`**，事件流只是它的实时视图（这条决定了断线、刷新、重连都不需要「补写」任何数据）。

### 3.3 目录结构

```
Avid/
├── pyproject.toml                     # 内核依赖仍只有 httpx；FastAPI 进 [project.optional-dependencies].web
├── src/avid/
│   ├── ai/
│   │   ├── config.py
│   │   ├── client.py                  # ①新增 stream_completion()（F3）
│   │   └── transcript.py              #   不变：messages 唯一所有者
│   ├── runtime/
│   │   ├── loop.py                    # 加 ask=/on_event= 两个关键字参数；加取消检查（2 处）
│   │   ├── state.py                   # RunState 加 ask / cancelled / cancel_reason
│   │   ├── execution.py               # PreToolUse context 带上 state.ask
│   │   ├── hooks.py                   # permission_hook 用 context 里的 ask，无则回落到 stdin
│   │   ├── events.py                  # ★新增：事件类型单点定义 + RunObserver
│   │   ├── context.py                 # 压缩发生时发事件（经 observer）
│   │   └── ...
│   ├── policy/                        # 不变（permission 的 ask 注入点已存在，permission.py:112）
│   ├── session/                       # 不变
│   ├── tools/                         # subagent.py 前传 ask（3 行）
│   ├── svc/                           # ★新增：应用服务
│   │   ├── __init__.py
│   │   ├── runs.py                    # 运行注册表：每会话至多一个活动 run、线程、事件缓冲、重放游标
│   │   ├── approvals.py               # 待决审批表 + 阻塞等待 + 超时失败关闭 + run 作用域白名单
│   │   ├── sessions.py                # 会话读：列表/元信息/条目分页（不含写）
│   │   └── errors.py                  # RunBusy / RunNotFound / ApprovalExpired 等
│   ├── web/                           # ★新增：传输适配（唯一 import FastAPI 的地方）
│   │   ├── __init__.py                # create_app()
│   │   ├── app.py                     # 装配、静态资源、SPA fallback、/api 404 不与 SPA 混
│   │   ├── schemas.py                 # pydantic DTO + 显式 mapper（内核类型是 dataclass，不是 pydantic）
│   │   ├── sse.py                     # 分档编帧：durable 带 id、transient/delta 不带
│   │   ├── static/                    # 构建产物（gitignore；由 web/ 构建后复制）
│   │   └── routes/
│   │       ├── meta.py  sessions.py  runs.py  events.py  approvals.py
│   └── cli.py                         # 加 `avid web` 子命令（起 uvicorn），其余不变
├── web/                               # ★新增：前端工程（pnpm，独立工具链）
│   ├── package.json  pnpm-lock.yaml  vite.config.ts  tsconfig.json  index.html
│   ├── scripts/
│   │   ├── check-layers.mjs           # 层依赖与网络出口禁令（§14 A8）
│   │   ├── gate-size.mjs              # 体积预算 + 豁免表（§14 C1）
│   │   └── copy-dist.mjs              # 产物 → src/avid/web/static，并写构建戳
│   ├── src/
│   │   ├── main.tsx  App.tsx  router.tsx
│   │   ├── api/                       # 唯一网络出口
│   │   │   ├── generated/             # OpenAPI → TS 类型（不入库时在 CI/本地生成）
│   │   │   ├── client.ts              # 统一 header、超时、错误映射、AbortSignal
│   │   │   ├── stream.ts              # SSE 连接、Last-Event-ID、退避重连、resync
│   │   │   └── queries.ts             # TanStack Query hooks（只放 REST）
│   │   ├── events/                    # 事件层（可无 UI 单测）
│   │   │   ├── types.ts               # 与 runtime/events.py 对齐的联合类型
│   │   │   ├── reducer.ts             # applyEvent：纯函数
│   │   │   ├── coalescer.ts           # delta 合并器（rAF，≤1 commit/帧）
│   │   │   └── __tests__/
│   │   ├── state/
│   │   │   ├── runStore.ts            # zustand：活动域（只由 reducer/coalescer 写）
│   │   │   ├── uiStore.ts             # zustand：界面域（持久化到 localStorage）
│   │   │   └── queryClient.ts         # TanStack Query 配置（重试、staleTime）
│   │   ├── ui/                        # 设计系统（涂鸦潦草风）
│   │   │   ├── tokens.css             # 唯一颜色/间距/字阶/动效来源
│   │   │   ├── sketch.css             # 涂鸦语言：--sketch-r* / --sticker-* / 墨线 / 点阵 / 笔触
│   │   │   ├── sketch/                # 手绘轮廓 SVG blob + 胶带/便签构件（≤6 个入口用）
│   │   │   ├── primitives/            # Button / Dialog / Field / Tooltip / Toast（含按压位移）
│   │   │   └── patterns/              # ToolCallCard / ApprovalBar / StepGroup / TodoPanel…
│   │   ├── assets/                    # 空：手写体已弃用，字体只剩系统栈（零自托管文件）
│   │   ├── features/
│   │   │   ├── conversation/  approvals/  sessions/  inspector/  composer/
│   │   ├── layouts/AppShell.tsx       # 三栏 + 响应式 + 键盘图
│   │   ├── routes/                    # URL ↔ feature 组合；唯一接线查询与 store 的地方
│   │   └── lib/
│   │       ├── markdown/              # 照 purrcat 的 MarkdownComponents 映射（§8.5）
│   │       ├── ansi.ts                # SGR 颜色/粗体渲染（不引 xterm）
│   │       ├── diff.ts                # 红绿笔 diff
│   │       └── i18n/                  # 单文件字典 + LocaleProvider（照 purrcat 结构）
│   └── e2e/                           # Playwright：a11y、键盘、视觉回归、首 token、长会话
└── tests/
    ├── test_run_events.py             # 事件序列与重放（无 UI）
    ├── test_event_contract.py         # 两侧事件清单一致（读 web/src/events/types.ts）
    ├── test_web_api.py                # 用 httpx ASGI transport 打端点
    ├── test_web_boundaries.py         # grep 类断言：内核不 import 框架、web 不写会话
    └── test_approvals.py              # 挂起/答复/超时/重复答复/取消
```

命名与归属理由：`svc/` 与 `web/` 都放在 `src/avid/` 内（与 `ai/`/`runtime/`/`policy/`/`session/` 平级），因为它们属于**内核包的运行方式**，不是独立产品；前端 `web/` 放在仓库根，与 `src/`、`tests/`、`docs/` 平级，因为它是独立的包管理器与工具链，放进 `src/avid/` 会让打包器把它当包数据处理。这一分配与 Chainlit（`backend/` 与 `frontend/` 平级、产物拷进 wheel）同形。

### 3.4 组件分层与依赖规则

五层，单向依赖，越往上越知道业务：

| 层 | 内容 | 约束 |
|---|---|---|
| L0 `ui/tokens.css` + `ui/sketch.css` + `ui/primitives/` | 涂鸦 token（形状/硬阴影/墨线/纸纹/倾斜）+ 无样式原语包装（Radix/Base UI） | 不得出现业务名词；不得 import `state/`、`features/`；六种形状与八个阴影档只在这里定义 |
| L1 `ui/patterns/` + `ui/glass/` | 有形状无状态：`ToolCallCard`、`ApprovalBar`、`StepGroup`、`EntryRow`；玻璃原语 `Backdrop`、`GlassTag` | 只接受 props；不得读 store、不得发请求 |
| L2 `features/*` | 一个业务面一个目录，`components/ hooks/ index.ts` | 可依赖 L0/L1；**feature 之间不得互相 import**（跨 feature 经 store 或 route 组合） |
| L3 `layouts/AppShell` | 三栏骨架、响应式、键盘图 | 可依赖含 `uiStore`；不得直接读 `runStore` |
| L4 `routes/` | URL ↔ feature 组合，接查询 hooks 与 store | 唯一允许把查询结果与 store 数据拼在一起的地方 |

五条可执行的规则（§14 A8/A9）：

1. 网络只能在 `src/api/` 里出现：`fetch(`/`EventSource(`/`new WebSocket(` 出现在其它目录即失败。
2. `features/a` import `features/b` 即失败。
3. `runStore` 的写入口只有 `events/reducer.ts` 与 `events/coalescer.ts`（其余模块 import 只读访问器）。
4. 颜色/间距字面量只能出现在 `ui/tokens.css`；`dark:` 修饰符与 Tailwind 内建调色板类一律报错。
5. **L1 只接受 props**：`src/ui/**` 不得 import `events/`/`state/`/`features/`/`routes/`/`api/`/`layouts/`。

第 5 条的由来：L1 曾经 import `events/reducer` 的 `TimelineEntry`/`ToolRun` 与
`state/uiStore` 的 `Density`（都是 `import type`，所以没有运行时环，但方向已经反了，
"patterns 可以脱离 store 单测"这句声明随之不成立）。修法是把**渲染用的视图类型**
下沉到 `lib/timeline.ts` 与 `lib/density.ts`：`events/reducer`（生产这些视图）与
`ui/patterns`（渲染它们）依赖同一份形状，且各自只有一条 import 路径。

这条「把架构约束写成会失败的检查」的形态取自 OpenHands 的源码正则测试（`dev/research/agent-frontend-survey-final.md:482`），代价是正则能力有边界——只匹配字面量，拼接出来的 URL 与间接 import 不在覆盖内；规则文档必须同时写明这条边界。

### 3.5 状态管理：三域切分

| 域 | 内容 | 机制 | 权威来源 | 失效方式 |
|---|---|---|---|---|
| 权威域 | 会话列表、条目历史、技能目录、运行状态 | TanStack Query（含 `useInfiniteQuery` 做条目分页） | `session/` 的文件 | stale-while-revalidate + 显式 invalidate |
| 活动域 | 当前 run 的实时视图：进行到第几轮、哪些步骤在跑、待决审批、delta 文本 | zustand 单 store + 纯 reducer + delta 合并器 | 事件流（durable 事件与权威域最终一致） | 断线重连 + 游标重放；无法补齐则 `resync` 重建 |
| 界面域 | 主题、栏宽档、密度、折叠默认值、过滤器、命令面板开关 | zustand（持久化到 localStorage） | 用户 | 本地，随版本迁移 |

切分规则与理由：

- **事件绝不写进查询缓存。** Dify 为混合两者付出了手写失效图的代价（final 报告记 1253 行、另一路复核记 1283 行，数字有出入但量级一致；每新增一个写操作都要手写失效边，漏写即脏数据，`dev/research/agent-frontend-survey-final.md:414`、`dev/research/agent-frontend-survey.md:795`）；LibreChat 同时存在 TanStack Query v4 与 SWR（`dev/research/agent-frontend-survey-final.md:143`）。**数据获取层只留一套**（LibreChat 与 LobeChat 的双轨并存是这条约束的由来，`dev/research/agent-frontend-survey-supplement.md:318`）。
- **服务端状态不在 localStorage 里留副本**：本地反面样本 purrcat 把画布图既 persist 到 `localStorage` 又存在服务端（`dev/tmp/purrcat-src/ui/src/store/flowStore.ts:42,304`），两份真相必然分叉。Avid 的界面域只存**纯 UI 偏好**。
- **服务端对象身份不由前端发明**：`entry_id`、`run_id`、`approval_id` 一律服务端生成。乐观渲染允许用临时 id，但 durable 事件到达时就地替换；本地反面样本 purrcat 在导出画布时随机重生成节点 ID（`flowStore.ts:207-211`），导致后端按 ID 记的状态失配。
- **取数位置纪律**：需要数据的组件自己取数（在 L2 `features/*` 的 hook 里），**禁止**在 route 顶层取数再逐层透传。这是调研里唯一写成文字的取数位置约束（Onyx 强制 `useSWR` 在使用组件内、pending 给 loader，`dev/research/agent-frontend-survey.md:828`）；反面是本地 purrcat，`ChatPage.tsx` 1799 行 / 104 个 `useState` 把全部数据从页面顶部透传下去。
- **渲染状态机 ≠ 调度状态机**：前端可以有 `idle | submitting | streaming | awaiting_approval | cancelling | done | failed` 的渲染状态（Onyx 的 `ChatState` 同形：`input|loading|streaming|toolBuilding|uploading`，`dev/research/agent-frontend-stack-survey.md:540`），但它**不决定何时继续或何时询问**——那是内核的事。「等待人工批准」在这套状态里是**独立状态**，不是一条普通消息；Continue、n8n、gemini-cli 三家独立收敛到这个形状（`dev/research/agent-frontend-survey-addendum.md:258-265`；**该文件已于 2026-09-22 清理中删除，锚点不可复核**）。

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

补充两个**前端侧协议**候选：Vercel AI SDK 数据流（AutoGPT、Dify 用）与 AG-UI（Langflow 用 `@ag-ui/client`，`dev/research/agent-frontend-survey-final.md:161`）。它们解决的是「前端怎么消费 agent 流」，若采用就得让 Python 侧产出第三方协议形状的事件，等于把 Avid 自己的事件类型让位给外部协议。**不采用**；重新考虑信号见 §16（出现第二个前端表面时，才值得把事件定义升级为独立 IDL，Cline 的 22 个 `.proto` / 223 rpc 是那条路的成本）。 

### 4.2 决策：FastAPI

**理由（按证据强度）**

1. 调研内 5 个 Python 后端的 agent 项目全部是 FastAPI/Starlette 系：OpenHands agent-server（`fastapi>=0.104` + uvicorn + websockets）、Open WebUI（已验证 FastAPI，生产关掉 `docs_url`/`openapi_url`）、Chainlit（FastAPI + Starlette + python-socketio）、AutoGPT（FastAPI，且拆成 REST 与 WS 两个 app）、Onyx（FastAPI + 反代 `/openapi.json`）。这是唯一有可复用证据面的选择，其余候选都要靠推断。
2. OpenAPI 自动产出直接满足「契约先于 UI」的最低成本路径：调研四种契约流派里，生成式的三个样本后端都是自家 Python（`dev/dev/research/agent-frontend-survey.md:188`）。
3. SSE 与 WebSocket 都在 Starlette 里，换传输不需要换框架——这使 §5 的传输决策保持可逆。
4. 它是 Starlette 的薄封装，**没有自带 UI**，与「内核归自己」的取向一致（与 Chainlit 的关键差别）。

**代价与对冲**

- 依赖簇变大（对照 `pyproject.toml:7` 当前的单一 httpx）。对冲：FastAPI 进 `[project.optional-dependencies].web`，CLI 使用路径不装；`web/` 是唯一 importer，由 §14 A1/A2 的 grep 门禁守着。换框架（例如退到 Starlette 单用）只需改 `web/`，`svc/` 与内核不动——这是 §2 里 `web/` 这个边界存在的理由。
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
- 产物漂移的对冲：`copy-dist.mjs` 写 `web/static/.build.json`（`git_sha` + `built_at`），`GET /api/meta` 返回它，UI 页脚显示；从 checkout 构建时，`tests/test_web_boundaries.py` 断言构建戳的 `git_sha` 等于 `git rev-parse --short HEAD`（在 CI/本地构建路径上跑，安装路径跳过）。
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

| 档 | 是否带 `id`/`seq` | 是否重放 | 例子 |
|---|---|---|---|
| durable | 是（run 内严格单调） | 是 | `run_started`、`user_message`、`assistant_message`、`tool_call_started`、`tool_call_finished`、`approval_requested`、`approval_resolved`、`context_compacted`、`todo_reminder`、`stop_nudge`、`run_finished`、`run_failed`、`run_cancelled`、`resync` |
| transient | 否 | 否 | `run_status`（当前轮次、累计 token、当前活动工具） |
| delta | 否 | 否 | `assistant_delta`（增量文本，F3 起才有） |

四条规则：

- **delta 不落盘、不进会话、不重放。** 必须先决定这件事，因为它决定「重放语义」：若 delta 落盘，UI 投影就要处理「半截文本 + 完整文本」两种记录，且 `session/` 的条目不可变假设会被打破。代价是刷新后正在流式的消息会以「等待中」出现，随后的 durable 消息补齐——这是可接受的，因为 durable 消息带完整内容。
- **delta 默认不投递，消费方显式订阅。** 事件流默认只发 durable + transient；`assistant_delta` 需要订阅方显式声明（例如 `?deltas=1` 或首帧订阅消息）。这条抄的是 OpenHands 的做法：`receives_streaming_deltas: ClassVar[bool] = False`，注释写明 "consumers opt in rather than inherit them"（`dev/research/agent-frontend-survey-verified-addendum.md:414-417`）。收益是「不关心增量的消费者」不必承担合并与重排的成本，而 F0 的事件层不需要等 F3 就能验收。
- **I5（不丢）**：每个 durable 消息事件携带**完整**内容（`assistant_message.data.content` 就是最终文本）。因此 delta 全丢也不影响正确性；这条同时是 a11y 的落点（§8.5）。
- **I4（单调）**：一个 run 内 durable 事件的 `seq` 由唯一发射线程分配，严格递增、不重复。

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
| `run_failed` | `LLMError` / `ConfigError` / `SessionError`（内核异常映射，见 A5） | svc |
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
| 前端侧 Markdown 增量渲染库（streamdown 类） | Dify 与 AutoGPT 都用它（`dev/research/agent-frontend-survey-final.md:221`），但它是 React 生态绑定且体积不小；v1 的消息是整条到达 | F3 接入 delta 后，若自研渲染的帧率不达标 |
| 前端文件写入 / 目录浏览 API | 本地反面样本 purrcat 的人类面板绕过权限模型直接写文件（`ui/src/components/chat/IDEPanel.tsx:411-415` 经 Electron `fs:writeFile`，或 `POST /api/filesystem/write` 只做路径转换 + `open().write`），而 agent 侧有 `require_write` 闸门——同一个仓库两套写路径 | 需要人类手动改文件时，**经工具管线**暴露（走 `write_file` 与审批闸门），而不是新开一条 API |
| 桌面壳（Electron/Tauri） | OpenHands 的打包链要同时带 uv 与 Node 分发（约 130 MB），并用 `afterPack` 把误拷的 `node_modules` 换成 7 MB 闭包（`dev/research/agent-frontend-survey-final.md:498`）。参照实现 purrcat 有完整的 Electron 壳可抄（`electron/main.js` 的 sidecar spawn + 看门狗 + 就绪轮询 + 超时错误页、`preload.js` 的窗口与 `fs:*` 桥），**但它的 `fs:readFile/writeFile/readDir/stat` 没有路径白名单，照抄前必须先加**（§8.5） | 需要在没有终端的机器上分发时 |
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

**一个被前端推到首屏的既有代价**：`GET /api/sessions` 要显示会话名与条数，而名字是会话文件里的一个值、条数要读全部条目，所以这个端点当前是 O(会话数 × 文件大小)——`cli.py:13-15` 已经承认了这个代价（`--list-sessions` 的 `_peek` 会打开每个会话，`cli.py:238-247`）。CLI 下它是「敲一次命令等一会」，Web 下它是**每次刷新首屏**。处置：v1 接受并把实测耗时记进性能基线；触发条件与既有结论一致——当会话数使首屏明显变慢时，把会话名冗余进 JSONL header（`cli.py:13-15` 已写明这条路径）。

**写权限的归属**（判据 §4/§6）：会话条目的写入者有且只有 `SessionRecorder`。因此 API 里**没有**「追加条目」端点——前端不是这些对象的作者。人类要写文件时，走 `/api/runs` 让 agent 去调用对应工具，权限闸门与审计因此不被绕过。同理，服务端**不把「是否执行」的判定委托给浏览器的可用性**：浏览器只是决策的输入端，未答复一律收敛为拒绝（I6）——这与 Open WebUI 的反向 RPC（后端 `sio.call` 阻塞等浏览器回包才决定是否执行，`dev/research/agent-frontend-survey-addendum-verified.md:196`）是相反取向，那样会把权限判定拆到两个信任域。

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
- **按特性分支，不按版本号分支**：客户端读 `features`（例如 `{"deltas":1,"approvals":1,"cancel":1,"branches":0}`）决定启用哪些能力，只在客户端构建的 `api_version` 与内核声明**不兼容**时才失败收敛。形态取自 OpenHands 的两层防护——构建期钉死版本 + 运行期按特性协商（`AGENT_SERVER_VERSION_TOO_OLD` + feature→minVersion 表 + `/server_info` 缓存，`dev/research/agent-frontend-survey-verified-addendum.md:418-423`）。特性表比单一版本号更耐漂移：加一个可选事件不会让所有旧前端罢工。
- 失败收敛的具体表现：显示「界面与内核版本不兼容，请重新构建 `web/`」并禁用提交，而不是尽力渲染（LibreChat 的协议协商就是这个形状，`dev/research/agent-frontend-impl-survey.md:274`；**该文件已于 2026-09-22 清理中删除，锚点不可复核**）。
- **机械检查（先做这个，不上生成器）**：`tests/test_event_contract.py` 解析 `web/src/events/types.ts` 的联合类型成员集合，与 `runtime/events.py` 的 `EVENT_TYPES` 比较集合相等。理由：生成式契约不是免费的——Dify 生成前要打 6 类规范化补丁、OpenHands 要维护公开面过滤 + 人工 `allowClientOnly` 清单并已出现生成源 1.47.0 与运行时 1.49.1 的静默漂移（`dev/research/agent-frontend-survey-final.md:41`）。在「事件数量 × 变更频率」超过人工同步成本之前，一条集合相等测试比一套生成器便宜（这条判据取自 `dev/research/agent-frontend-survey-final.md:478`）。
- **升级到生成器的条件与路径**（写清以便将来照做）：事件类型 ≥ 25 个，或单次迭代要改 ≥ 3 个事件的载荷结构时，改用 FastAPI 的 OpenAPI 做**类型生成**（hey-api，只生成类型不生成方法体，OpenHands 的形态），门禁用 Dify 的「CI 先删再生成再 diff」（`dev/research/agent-frontend-survey-final.md:118`）。
- **破坏性变更的跑道**（生成器时代才需要，现在记录以免将来临时发明）：OpenHands 的做法是 5 个 minor 版本的弃用跑道 + 用 `oasdiff` 对比上一个 PyPI 发布 + CI 校验弃用话术；弱 schema 的允许清单必须带 `reason` / `owner` / `expiry` / `follow_up` 四个字段（`dev/research/agent-frontend-survey-verified-addendum.md:435-439`）。Avid 当前的规模不需要它，但**契约一旦开始生成，废弃就必须有到期日**，否则抽象会永久滞留。

---

## 7. 内核需要先补的东西

七处，总量估计 300–450 行代码 + 测试。**只有第 4 项碰调度**。

### 7.1 `runtime/events.py`：事件类型单点 + 观察者（必做，F0）

```python
EventType = Literal["run_started", "user_message", ..., "resync"]
EVENT_TYPES: tuple[str, ...] = (...)          # 单点
@dataclass(frozen=True)
class RunEvent:                                # 普通 dataclass，不是 pydantic
    type: str; run_id: str; seq: int | None; ts: int; data: dict
class RunObserver(Protocol):
    def __call__(self, event: RunEvent) -> None: ...
```

`agent_loop(..., on_event: RunObserver | None = None)`：`on_event` 只承载**循环自己才知道**的事实（轮次开始、TODO 提醒、Stop nudge、结束/取消），其余全部来自既有 hook。**这不是「第二个观察点」**：`on_message` 仍是消息的唯一通道，`on_event` 是步骤级事实的通道；两者都不改调度。代价：事件类型公开后即承担兼容责任，所以 F0 的验收只要求「能在不启动 UI 的情况下订阅并断言」，UI 是第二个消费者（顺序取自 `dev/research/agent-frontend-survey-final.md:472`）。

### 7.2 审批注入（必做，F1）

现状的证据链：`permission_hook` → `check_permission(name, arguments)`（`hooks.py:131`）→ 默认 `ask_user`（`permission.py:128`）→ `sys.stdin.readline()`（`permission.py:101`）。**在由 uvicorn 启动的进程里，stdin 不是终端**，这条路径会读到 EOF（直接拒绝）或阻塞；而且 `_ASK_LOCK`（`permission.py:60`）是模块级全局锁，会跨会话互相阻塞。

改动（4 处，都是把已有参数接上）：

1. `RunState.ask: AskUser | None = None`（`state.py:24-45`），`for_run(auto_approve=…, ask=…)`。
2. `agent_loop(..., ask: AskUser | None = None)`（`loop.py:83-97`）→ 传入 `RunState`。
3. `execution.execute_one` 把 `state.ask` 放进 `PreToolUse` 的 context（`execution.py:70-75`）。
4. `permission_hook` 优先用 `context.get("ask")`，没有则回落到 `ask_user`（CLI 行为逐字不变）。

加上 `subagent.py` 的前传（`run_subagent(..., ask=None)` 与 `subagent()` 从 `state.ask` 取值，各 2 行）：否则子 agent 的审批会落到 stdin 上——这是**当前就存在的隐含缺陷**，只是 CLI 下被 `_ASK_LOCK` 与共享终端掩盖了。

`AskUser` 的签名保持 `(name, arguments, reason) -> bool`（`permission.py:54`），不改类型：Web 侧要在「允许一次 / 本 run 内对同一工具总是允许 / 拒绝」之间选择，做法是**注入的 ask 回调内部维护 run 作用域白名单**，命中时直接返回 `True` 而不发起请求。这条把「策略」放进了协议适配层；依据是 `ask` 参数本就是策略注入点（`permission.py:112`），删除测试：删掉这个白名单，用户每次都要重新批准（体验变差但不破坏边界）。**准入证据**：出现第二个需要同样语义的调用方（例如桌面壳）时，才把它上移到 `policy/permission.py`。

### 7.3 压缩事件（必做，F0，最小改动）

`context.announce()` 已经有 `CompactReport` 与计数（`context.py:48-56`），只需让它把 report 交给 observer（`announce` 多一个可选参数，或在 `RunState` 上挂一个 observer 引用）。**为什么要做**：gemini-cli 把 `chat_compressed` / `context_window_will_overflow` 这类上下文治理事件放进了它 18 个事件的枚举（`dev/research/agent-frontend-survey-final.md:408`）——压缩、循环检测、权限阻塞这些内核行为必须可被 UI 观察，否则 UI 只能把它们渲染成没有解释的等待。

### 7.4 取消（必做，F1；唯一碰调度的一处）

`RunState.cancelled: bool` + `RunState.cancel_reason: str | None`；`loop.py` 在**两个位置**检查：每轮开始前与每批工具执行前。命中则抛出 `RunCancelled`（终止原因，不是错误），svc 捕获后发 `run_cancelled`。取消是**唯一**的提前结束路径——循环没有轮数上限，也没有别的"未完成"终止原因。

- **粒度**：一个步骤。取消发生在「在飞的模型调用返回后」或「在飞的工具调用结束后」；最坏等待是一次模型调用（`TIMEOUT_SECONDS = 60.0`，`client.py:16`）。这个粒度必须写进 UI 文案（「将在当前步骤结束后停止」），不能承诺立即停止。
- **不丢已产生的消息**：消息在产生时就经 `on_message` 落库（`recorder.py:42-47`），所以取消不需要补偿写。
- **被否掉的方案**：用注入的 `chat` 包装器（`loop.py:90` 允许）在每轮前抛异常 + 用 `PreToolUse` 在取消时 BLOCK。理由：hook 抛异常按 BLOCK 处理（`hooks.py:83-86`），于是取消会在 transcript 里塞进一条「被拒绝的工具调用」消息并被提交，而那条消息对模型与后续续接都是噪音；而且取消状态会散在两个适配器里。**代价**：这是全文唯一为前端改调度的地方，所以它必须配一条不变量（I9，§11）与两个检查点的测试。

### 7.5 流式模型调用（F3，可选）

`ai/client.py` 增 `stream_completion(config, messages, *, system, tools, max_tokens, on_delta, client=None) -> Turn`：用 `httpx.Client.stream` 解析 `stream: true` 的 `data:` 行，累加 `content` 分片与 `tool_calls` 分片（按 `index` 归并），最后返回与 `parse_turn` **同形**的 `Turn`。

- **不需要动循环**：`chat` 是注入参数（`loop.py:90`），svc 传入一个绑定了 per-run 回调的 wrapper 即可。
- **难点写清**：`tool_calls` 的参数是分片流式到达的，必须按 index 拼接后再 `json.loads`；这是本项目里最容易写错的一段，所以累加器要做成纯函数并配 fixture 测试。
- **验收**：同一段 mock SSE 与同一份非流式 JSON 必须产出逐字段相等的 `Turn`（§14 B9）。
- **不做的前提**：v1 的 UI 在非流式下也必须是正确的——渲染状态机在「等模型」时显示步骤占位，durable `assistant_message` 到达即替换（并因此不需要 delta 就能验收 B1/B4/B7）。

### 7.6 会话「上次运行中断」的可见性（不做，靠投影）

服务重启会杀掉 run；客户端发现 `GET /runs/{id}` 404 且会话条目链尾是一批没有结果的 `tool_calls`。`session/projection.py:41-72` 的 `repair_incomplete_batches` 本来就会把这批丢掉以保证续接合法。UI 不需新字段：条目 API 可附带 `truncated_tail: true`（由同一次投影计算得出），前端在链尾显示一行「上次运行在此中断」。**不新增持久化字段**，因为这件事是**派生**的。

### 7.7 不做：任务图的写路径（已随任务图下线，阶段 27）

不加任何人类侧的任务写入端点（§6.1）。理由与反例见 §5.5。

阶段 27 把任务图整体下线——六个任务工具、`tools/tasks.py`、`svc/tasks.py`、
`web/routes/tasks.py`、`GET /api/tasks{,/{id}}` 与 `/tasks` 页面一并删除，本节记的这条非目标
因此没有了对象。保留它只为留下两件事：当时为什么不给人类写路径（写入者是 agent，加一条写
路径就要重新论证它是否绕过存储层校验），以及"这次对话的计划"现在由 `todo_write` 与输入条上方
的待办清单承接（`runtime-architecture.md` §17 有下线说明）。旧的 `<工作区根>/.tasks/` 数据不
迁移、不删除，只是不再被读。

---

## 8. 界面美观性如何落地：暖羊皮纸 + 液态玻璃

**视觉方向由直接指令确定过两次**：v1 参考 purrcat 的涂鸦潦草风（阶段 14 冻结）；阶段 23b
由需求方给出新的参照物（一张产品截图）并要求**融入液态玻璃**。本节记录第二次替换后的语言、
它的值来源与实测依据。

> 前一次的语言（涂鸦九机制：手绘圆角 / 硬偏移阴影 / 墨线 / 纸纹 / 倾斜 / 手绘轮廓 /
> 笔触填充 / 胶带）**已整体删除**。撤掉它的判据是 23b 的 G1：视觉语言的名字原先散在
> 21 个 L2 文件、5 个 e2e spec 与 33 条钉死取值的断言里，于是「换风格」必须动展示层。
> 类名中立化先做（零像素变化），让后面的替换只动 L0/L1。

### 8.0 参照物的量化体检

参照物是需求方提供的一张产品截图（2547×1378，DPR≈2）。体检结论：

| 维度 | 实测 |
|---|---|
| 底 | `#f2e7d0` 羊皮纸（占 **50.7%**）——**暖色浅底**，不是常见的深色玻璃 |
| 面 | 侧栏 `#ecdfc2`（深一档）/ 顶栏 `#f4e9d1`（浅一档）/ 输入条 `#fef7e8`（最亮） |
| 边 | `#cdc1aa` 1px；次级 `#e1cdaf` |
| 墨 | `#3a2a18` **深褐**而非纯黑 → 与底 11.23:1 |
| 面板质地 | 面板内部通道标准差 ≈ 0：**它是不透明平涂，不是玻璃**——玻璃是要加进去的，不是它已有的 |
| 背景 | 一幅整幅手绘插画覆盖页面中部大部（无文字区 sd 17–40、深色像素 1–6.5%） |
| 阴影 | 极弱或没有：它靠 1px 边框与一档色差分层 |
| 密度 | 小而密（右侧卡片区行距约 20 CSS px） |

三条从参照物直接推出的机制结论：

1. **模糊只能模糊背景里已有的东西**。纯平底色上，玻璃只剩「高光边 + 投影」能被看出来；
   那点「液态」的错觉需要背景有明暗变化。参照物用一幅插画提供它，我们用**纯 CSS 光斑
   网格**顶替（插画不进仓，见 §8.9 的预算条目）。
2. **参照物的质感来自插画与色温，不是来自阴影**。所以高度层级改由**投影的远近与软硬**
   承担，而不是旧语言那种"硬偏移阴影的位移量 = 层级"。
3. **密度不跟参照物**：它是信息密度偏高的多面板工具，而 Avid 是读长工具输出与 diff 的
   界面，保持既有字阶与行距。

### 8.1 玻璃语言：九个机制

全部只用 CSS（`backdrop-filter` + 渐变 + `box-shadow`），**不用位图纹理、不用 SVG 滤镜、
不旋转任何东西**。

| 机制 | token | 规则 |
|---|---|---|
| ① 玻璃面 | `--glass-face`（`--avid-glass-rgb` @ `--glass-alpha`） | 四档面共用 |
| ② 高光边 | `--glass-edge`（`--stroke-hair` + `--avid-edge-rgb` @ `--glass-edge-alpha`） | 1px 白边；`--glass-edge-soft` 给身份标签 |
| ③ 高光带 | `--glass-sheen` | 上边一道亮线 + 下边一道极淡暗线：玻璃的"厚度"全在这两行 |
| ④ 模糊 | `--glass-blur` + `--glass-saturate` | **只给面板类**（数量上限见 §8.9） |
| ⑤ 投影三档 | `--lift-1/2/3` | 高度 = 投影的远近与软硬；hover 抬一档、按住收掉（不位移） |
| ⑥ 光斑背景 | `--avid-blob-1..4-rgb` 四道径向渐变 | 纯 CSS；`ui/glass/Backdrop`，`aria-hidden` |
| ⑦ 圆角四档 + 胶囊 | `--r-chip/face/card/panel` + `--r-pill` | 10 / 12 / 18 / 24 px；**不再有形状轮换** |
| ⑧ 身份标签 | `.id-tag` / `.id-tag-empty` | 取代胶带：承担「当前是哪个 run/会话」的可见性（**功能不是装饰**，无倾斜） |
| ⑨ 图标 | lucide，`strokeWidth 1.75` | 两枚角色标记用 `Bot` / `User`；不自己画一套图标集 |

层级刻度（`--z-*`）不变。旧语言的 `--sketch-*` / `--sticker-*` / `--tilt-*` / `--hatch-*` /
`--paper-*` / `--font-sketch` 全部删除，没有留成"备用"。

### 8.2 色板、对比度红线，以及修掉的 9 处旧缺陷

| 角色 | token | 值 | 允许用途 |
|---|---|---|---|
| 页面底 | `--avid-paper-rgb` | `246 236 216` | 光斑底 |
| 光斑 | `--avid-blob-1..4-rgb` | `255 247 232` → `230 198 156` | 背景层（最深的一档决定最坏底色） |
| 玻璃面 | `--avid-glass-rgb` | `255 252 244` | 四档面 |
| 次级板 | `--avid-panel-rgb` | `236 223 194` | 凹陷 / 次级 |
| 墨 | `--avid-ink-rgb` | `58 42 24` | 边框、正文、投影 |
| 次级墨 | `--avid-ink-muted` | ink @ 73% | **唯一**允许的次级文字档 |
| 强调 | `--avid-accent-rgb` + `-deep` / `-soft` / `-ink` | `201 123 63` / `161 96 45` / `207 137 84` / `138 82 39` | 实心底配浅字 / 浅底配墨字 / 作为文字 |
| 状态 | `-bg` 与文字两套 | ok / warn / danger / info | `-bg` 只做填充与边框；文字是压暗伙伴 |

**对比度红线由 `check:contrast` 机械守住**：它按 alpha 合成跑 token 表里**声明**的 17 对
（取值全部从 `ui/tokens.css` 读，不写字面量），门槛 4.5、设计目标 **4.8**——目标是 4.8 而不是
4.5，因为"刚好通过"是巧合不是设计。最坏底色 = 最深光斑 + 玻璃层（模糊只会把极端值往均值拉，
所以这样取是保守的）。

顺带修掉的是**旧设计里 9 处从未被验过的不达标**。旧文档声称修掉了 purrcat 的对比度缺陷，
但状态色自己没被算过：

| 配对 | 旧实测 | 处置 |
|---|---|---|
| 四色状态文字 on 纸面 | 3.25 / 2.73 / 3.93 / 3.87 | 压暗到 4.81–4.86（对最坏玻璃底） |
| `ink/60` `/50` `/40` | 3.70 / 2.85 / 2.24 | 删掉散档，只留 `ink-muted`（5.34） |
| `Button` 的 `bg-accent text-ink` | 4.19 | 实心强调底改 `--avid-accent-deep-rgb` + `text-card` |
| `ToolCallCard` 的 `text-accent` | 2.52 | 改 `--avid-accent-ink-rgb` |

一条容易搞反的规则：**给深色填装配墨字要提亮，不是压暗**（压暗只会让对比度更低）。
danger 的浅底因此从 `#bf616a` 提到 `#ce858c`（3.37 → 4.83）。
状态色永远「深色文字 + 浅色底」，禁止自创「浅色文字 + 深色底」。

### 8.3 字体：两族，标题靠字重

1. `--font-sans`：系统栈承担全部正文、标题与中文。
2. `--font-mono`：代码、工具输出、ID、路径。
3. **弃用自托管手写体**（旧方案的 13,156 B 拉丁子集）。理由：手写体只覆盖拉丁与数字，
   中文永远走系统栈，于是"标题用另一种字体"这件事本身只在西文成立、中英混排必然突兀。
   新语言里标题由**字号 + 字重**区分（h1–h3 显式 `font-semibold`）。`font_bytes` 预算
   据此重冻为 **0**（§9）。
4. 构建期断言不变：产物与源码零 `fonts.googleapis` / `fonts.gstatic` 请求。
   `check:tokens` 要求每个字体 token 要么有 `@font-face`、要么是系统栈——它在 23b 直接
   拦下了「`--font-display` 别名到 `--font-sans`」这种空操作写法；处置是**按门禁的意图把
   那个角色整个删掉**，而不是放宽门禁。

### 8.4 布局：光斑画布 + 弹性对话卡 + 玻璃检查器

```
┌──────────────────────────────────────────────────────────────────────┐
│ 光斑画布（absolute inset-0，整页，aria-hidden）                        │
│ ┌────────────┐ ┌──────────────────────────────┐ ┌──────────────────┐ │
│ │ 导航列 320px │ │ 对话卡（flex-1，侧栏开时 420px）│ │ 检查器卡 420px     │ │
│ │              │ │ ┌ 头部：标题 + 状态 + 轮次/token + 常驻 ID 标签  │ │ 默认收起        │ │
│ │ 会话列表     │ │ ├ 时间线（唯一滚动容器）        │ │ resize-y 调高度   │ │
│ │ 技能目录     │ │ │  用户便签 / 步骤组 / 工具卡   │ │ 全文 / diff /     │ │
│ │ 设置        │ │ │  审批卡（徽标 + 展开原因）     │ │ 原始 JSON         │ │
│ │              │ │ └ 底栏：待办清单 + 输入条         │ │                  │ │
│ └────────────┘ └──────────────────────────────┘ └──────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
        侧栏打开时隐藏导航列（purrcat ChatPage.tsx:1234）
```

- **卡片叠放规则**：四档面（`surface-main` / `surface-panel` / `surface-card` / `surface-chip`）+ 投影三档 `--lift-1/2/3`。**高度由投影的远近与软硬表达**（旧语言是硬阴影的位移量）；面本身靠 `--glass-face` 的半透明与 `--glass-edge` 的高光边分层。
- **弹性而不是固定三栏**：对话卡 `flex-1 min-w-[420px]`，检查器出现时压到 420px 并 `transition-all duration-300`（照 purrcat `ChatPage.tsx:1237`）。窄屏时检查器升格为全屏覆盖层。
- **检查器高度用原生 `resize-y`**（purrcat `ChatPanels.tsx:18` 的 `h-[55vh] min-h-[35vh] max-h-[85vh] resize-y`）。这比我原稿的「不做可拖拽分栏」更进一步：**不引 `react-resizable-panels`，但保留了人调高度的能力**，且宽度不调（避免与 §8.6 的断点争权）。
- **时间线是唯一滚动容器**：导航与检查器各自滚动。吸底规则：用户上滚后不抢滚动，回到底部 40px 内才恢复。
- **ID 可见性**：会话/运行 ID 以玻璃标签（`ui/glass/GlassTag`）常驻卡片头部；多标签页或长会话里「我在看哪个 run」是实际需求。**这是功能不是装饰**——它随旧语言换了形式，但没被删掉。
- **导航有两种，不要都做成路由**：照 purrcat 的分工——**换工作面的走路由**（会话、技能目录），**同一个工作面里的子面板就地切换**（purrcat 点 MCP/Skill/Cron/Sensor 时不跳路由，只切 `sidebarMode` 并留一个「返回」按钮，`ChatPage.tsx:129` + `ChatSidebar.tsx:33/80/114/139/162`）。对 Avid：`inspector` 的三个视图（工具全文 / diff / 原始 JSON）属于就地切换，不进 URL 历史——否则浏览器后退键会被面板开关塞满。
- **每条消息带一行动作**：Avid 的条目动作是「复制」「从此处分支」，现在是两枚 44px 的**图标按钮**（`size="icon"`，`Copy` / `GitBranch`，图标 `aria-hidden`、名字在 `aria-label`）。动作行**常驻可见**（曾经的 `opacity-0` + 悬停显形已撤销：显隐只是额外一层谜，「有这功能」不该先被猜到）；文字说明改由悬停/聚焦时的 `Tooltip` 气泡承担，**浮在按钮外面**、未悬停时不挂载，于是"看得见的功能"不再以常驻一行文字为代价（窄卡片里那行还会换行）。条目级的「查看原始 JSON」已删，检查器改由工具卡的「查看」打开。
- **非 main 分支只读**：purrcat 在子分支上把输入区换成只读提示（`ChatPage.tsx:1566-1568`），避免把消息写到错的分支。Avid v1 只在 `main` 上跑运行，这条在 F4 引入分支视图时照做。
- **入口**：v1 **不做首页**。purrcat 的首页（大号手绘 blob 按钮 + 斜贴设置钮）好看，但对单用户本地工具是一次多余点击；一级切换由顶部工具按钮 + `⌘K` 命令面板承担。**重新考虑信号**：出现 3 个以上互相独立的工作面（例如未来的 fork 视图、评测看板），那时首页才是导航而不是仪式。

### 8.5 页面与组件清单（purrcat → Avid）

| purrcat 的页面/组件 | Avid v1 | 说明 |
|---|---|---|
| `ChatPage`（左侧栏 + 对话卡 + 头部工具条 + 侧面板） | **做**：`features/conversation` + `layouts/AppShell` | 主表面 |
| 头部 40×40 工具按钮 + `absolute -top-2 -right-2` 计数徽标 | 做：时间线/检查器/审批三个切换 + 徽标 | 徽标 + `animate-pulse` 表示有未决项 |
| `pendingReqs` 审批队列（徽标 + pulse + 展开 reason + 逐条裁决） | **做**：直接对应 §5.4 的审批队列 | purrcat 已给出可用交互形状；Avid 在此之上加「默认焦点在拒绝」与幂等（I6、B5） |
| `FileChangesPanel`（列表 + diff + 逐项 ack/rollback + ack all + 空态） | 做「看」的部分：检查器里的变更/diff 视图 | ack/rollback 属策略，v1 不做（§5.5） |
| 终端面板（多 tab、**折叠时保留 DOM 与连接**、重新展开要 re-fit） | **不做**（v1 不提供 Web 终端）；`bash` 输出按工具卡渲染 | 若要重做，`ChatPanels.tsx:146-425` 的「折叠不杀进程」是要点 |
| `IDEPanel`（独立窗口 + Electron IPC 文件读写） | **不做** | 与 §5.5「不提供文件写 API」直接冲突 |
| `TaskPage`/`MemoryPage`/`MarketPage`/`EvolvePage`/`EditorPage` | 都不做 | Avid 没有长期记忆、市场、自我进化、DAG 编辑这些对象；`TaskPage` 在阶段 27 之前做过一版只读任务板，随任务图一起下线 |
| `ConfigModal`（多标签配置） | 做 `features/settings`（读 `/api/meta`；改配置仍走 CLI/环境变量） | 前端不改 `.env` |
| Electron 壳（preload + 窗口控制 + 32px 拖拽条） | 不做（§5.5） | 真要做时照 purrcat，但**必须补路径白名单**：它的 `fs:readFile/writeFile/readDir/stat` 无白名单。另：`-webkit-app-region` 与那条 32px `z-[2147483647]` 在浏览器里会挡住顶部点击，必须只在 `html[data-shell="desktop"]` 下生效 |
| `MarkdownComponents`（标题带下划线、引用左边框、行内码带框、代码块带底、表格带框） | **做**：`lib/markdown/` 的直接蓝本 | 差异：代码块是深底等宽块（`.term`），标题用 1px 下划线与字重而非墨线，`pre` 必须 `overflow-x-auto`，长命令行不能被卡片撑破 |

每个 `ui/patterns/*` 组件必须在同目录 README 里声明四件事，缺一不算完成：**状态**、**密度**（compact 时间线 / comfy 检查器）、**折叠默认值**、**a11y 契约**（accessible name 来源、`aria-expanded`、错误态 `role="alert"`）。**没有 Storybook**（引入它要一整套构建与依赖，与零新依赖的取舍冲突）：视觉基线（§8.9）承担「长什么样」的回归，e2e 断言承担「机制还在」的回归。

**工具卡的两态形状**：折叠态是 `w-fit` 的小 chip（`surface-card`，工具调用用 `--avid-mark/40` 底显示 `CALL: <name>`）；展开态是 `w-full` 的玻璃卡 + 等宽内容（`.term`）+ 显式「收起」按钮。这样时间线在默认状态下只有「谁被调用、返回了没有」两行信息——打开会话看到的应该是结论而不是噪音。

**「思考中」是独立卡片，且折叠偏好要记住**（同 `ReasoningBubble`，`:248-331`）：进行中显示 `PROCESSING…` chip 并提供暂停；展开时是全宽、`max-h-72 overflow-y-auto`、自动滚底；结束后退化为浅色 chip。折叠偏好写 `localStorage`（purrcat 用 `purrcat-thinking-expanded`），避免用户每次都要重新折叠。对 Avid 而言这一层承载 `transient` 事件里的轮次/工具进度，以及 F3 之后的 delta。

**时间线的滚动与分页策略**（purrcat 已实现，比「不虚拟化」这个结论有用得多，`ChatPage.tsx:413-466`）：

1. **分组窗口化**：一组 20 条，首屏只渲染尾部，顶部一个「加载更早」按钮。
2. **碰顶一次只加载一组**，且需要离开顶部（`scrollTop > 150`）并经过 **400ms 冷却**才重新武装——惯性滚动不会连锁触发多次加载。
3. **插入历史后用 `useLayoutEffect` 做高度补偿**，把视口钉在原位置，内容不跳动。
4. **贴底阈值 50px**；切换会话/分支时瞬时贴底，其他情况不抢用户的滚动。
5. 该策略取代了「先上虚拟列表」：在 1000 条量级下先测它是否够（§9 的 P6/C5 判据据此改写）。

**命令对象带序号**（purrcat `ChatPage.tsx:124-127`、`ChatPanels.tsx:346-361`）：触发动作的状态存成 `{seq, cmd}` 对象而不是字符串——「再取消一次」「再批准一次」这种同值重发会因 React 的 `setState` 等值 bail out 而静默失效。Avid 的取消与审批按钮同样要带一个递增序号（服务端的幂等由 `approval_id` 承担，前端的序号只负责让重复点击真的到达服务端并被 `accepted:false` 挡回）。

**输入条**（purrcat `ChatPage.tsx:1496-1565`）：多行 textarea、`Enter` 发送 / `Shift+Enter` 换行、发送键 `min-h-[80px]`、附着 chips 行。照做两点：**提交按钮在提交中必须互斥禁用**（busy 布尔，purrcat `ConfigModal.tsx:116,123`）；**未发送的草稿要能扛住刷新**（purrcat 编辑器用的是「草稿 + 10 分钟 TTL」恢复，`AgentLoopEditor.tsx:141-152`——Avid 的输入条同样值得有，但它只是 UI 便利，不进会话）。

**Markdown 与工具输出的净化**（两条现成的坑，purrcat 都踩过并修好）：① 工具结果可能是 `{content, metadata}`、多模态 parts 或 `{error}`，必须先过一个**净化容器**再进 `whitespace-pre-wrap`（`ChatShared.tsx:144-168`）；② 本地路径与 `file://`/`term://` 链接需要 `urlTransform` 白名单，并且要取 `getAttribute('href')` 的原始串——否则浏览器会把 `D:/x.png` 规范化成站内 URL（`ChatPage.tsx:9-28,355-367`）。Avid 只放行 `http(s)://` 与工作区相对路径，且默认不自动打开外部链接。

- **按工具分发渲染**：`bash` → 等宽 + 退出码 + 截断标记 + **ANSI 转义渲染层**（purrcat 靠 xterm 承担，我们不引 xterm，用一个只做 SGR 颜色/粗体的渲染器）；`read_file`/`write_file`/`edit_file` → 红绿笔 diff；`glob` → 路径列表；`todo_write` → 工具卡（历史记录）+ 输入条上方的待办清单（当前计划，阶段 27）；`subagent` → 子任务分块（`=== i/n · description ===` 就是卡边界）；`load_skill` → 技能名 + 取回字符数。原稿在这里还列过 `create_task` 系列 → 任务卡，那六个工具随任务图在阶段 27 一起删除，分发里不再有这一支。
- **连续工具调用自动成组**：阈值 `EVENT_GROUP_MIN_SIZE = 2`，失败或拒绝的调用不进组。
- **文件规模**：单文件 ≤200 行、单组件 ≤50 行（`catch` 必须处理或注明）。这是对 purrcat `ChatPage.tsx` 1799 行 / 104 个 `useState` 的直接纠正。

### 8.6 响应式与可缩放

三档，用视口断点（**未验证假设**：单窗口单页，不需要嵌进别人的容器；若要嵌入宿主页面，改成容器查询 + `postcss-prefix-selector` 把全部 CSS 前缀到 `[data-avid-ui]`，OpenHands 是现成路径）：

| 档 | 宽度 | 布局 |
|---|---|---|
| 宽 | ≥1280px | 导航 320 + 对话卡自适应 + 检查器 420（可收起） |
| 中 | 960–1279px | 导航折叠为 64px 图标轨；检查器变右侧抽屉覆盖对话卡 |
| 窄 | <960px | 单列；导航变顶部 sheet；输入条固定底部（光斑背景不随断点变化：它没有高频细节，缩小不产生噪音） |

- **触控目标 ≥44px**：用 `max(var(--avid-control-height), 2.75rem)`，不写死像素。
- **全局文本缩放**：`html { font-size: calc(1rem * var(--avid-text-scale, 1)) }`，设置面板四档 90/100/110/125%。**注意**：旧语言的硬阴影位移本身就是层级线索，所以必须随缩放一起走；新语言的投影是柔光（远近与软硬），不参与缩放——那条像素级依赖随之消失。
- **不假设桌面**：桌面壳专属样式（拖拽条、`app-region`）单独放 `layouts/shell-desktop.css`，默认不加载。

### 8.7 可访问性（新语言特有的五个风险）

| 风险 | 具体表现 | 处置 |
|---|---|---|
| alpha 文本对比度 | 半透明玻璃把底压暗，散落的 alpha 档在它上面普遍不达标（实测 `ink/40` 只有 2.24:1） | `check:contrast` 按 alpha 合成算 17 对；文字只允许 `ink` 与 `ink-muted` 两档（§8.2） |
| 倾斜损害可读性 | （旧语言的风险，已随机制消失） | 新语言**不旋转任何元素**：倾斜 token 与全部 `tilt-*` 类已删除；`check:style` 的 rotate 禁令保留——它现在守的是「别再引入倾斜」 |
| 动效与「物理感」 | `hover:-translate-y-1`、`active:translate-y-2`、`animate-[spin_3s_linear_infinite]`、`animate-pulse` | `prefers-reduced-motion: reduce` 下位移、旋转、脉冲全部归零（保留颜色变化，因为它是状态信息） |
| 纯装饰元素与读屏器 | 手绘 blob、胶带、点阵对读屏器是噪音 | 装饰层 `aria-hidden="true"`；按钮的 accessible name 来自内部文字（purrcat 的大按钮内部有 `<h2>`，这点是对的） |
| 大面积模糊与遮罩 | 模糊是玻璃的核心机制，而它在「面积大 + 背景有细节」时是长任务来源（§8.9 实测：插画底下 1 次 57 ms） | 同屏可模糊元素数 ≤12、**列表内不模糊**；Dialog 遮罩用 `bg-ink/40` 纯色，不叠模糊 |

其余照既有约定：`aria-live="polite"` 只挂「已完成的助手消息」（delta 不进 live region，这是 I5 的推论）；键盘可完成主任务（`Enter` 发送 / `Shift+Enter` 换行 / `Esc` 取消 / 审批对话框 `Enter` 允许、`Esc` 拒绝、**默认焦点在拒绝** / `⌘K` 命令面板）；SPA 路由切换后焦点移到主标题；`sr-only` 跳过链接（照 Open WebUI）；对话框统一用 `ui/primitives/Dialog` 实现 focus trap（purrcat 有约 18 个手写 `fixed inset-0` 壳，无 Escape、无焦点陷阱）。

### 8.8 文案与 i18n：照 purrcat 的结构，只出中文一份

照抄 purrcat 的**结构**（`src/i18n.tsx`）：单文件字典、`LocaleProvider` + `useTranslation()`、`t('chat.switchSession')` 点分 key、localStorage 持久化、`documentElement.lang` 同步、命名空间分组。Avid 的命名空间：`common / chat / tools / approvals / todos / sessions / skills / errors`（原稿里还有 `tasks`，随任务图在阶段 27 下线）。

v1 只出一份 `zh-CN`，但**按双语字典的形状写**（顶层就是 `{'zh-CN': {...}}`），将来加语言是补一个对象而不是重构。两处 purrcat 的不一致要避开：① 它是中文项目却把默认语言定成 `en-US`，且 `index.html` 硬编码 `lang="zh-CN"` 之后又被运行时覆盖——Avid 默认 `zh-CN`，并让 `index.html` 的 `lang` 与默认值一致（否则首屏会闪一次语言切换，读屏器也会先按错的语言发音）；② 它的 `t()` 缺失时回落 `en-US`、再缺返回 key 本身，这个回落链保留，但**回落到 key 本身必须在开发模式报错**（否则漏翻只在用户眼前暴露），这也正好接上「lint 禁止 JSX 内联字面量」那条。lint 禁止 JSX 内联字面量（Onyx 的 `i18n/no-raw-jsx-text` 形态）。

### 8.9 玻璃的代价与视觉回归

**代价实测**（2026-09-22，阶段 23b；203 条目的长会话、1440×900、headless Chromium；
可复现命令见 `web/scripts/measure-glass.mjs`）：

| 配置 | 帧间隔 p50 / p95 | 长任务 |
|---|---|---|
| 现状（面板类带 blur） | 16.0 / 17.0 ms | 0 次 |
| 关掉 blur | 16.0 / 17.0 ms | 0 次 |
| 再关掉投影（零玻璃基线） | 16.0 / 17.0 ms | 0 次 |
| 违反「列表不模糊」（时间线条目也 blur） | 16.0 / 17.0 ms | 0 次 |
| 现状 + **运行期插画底** | 16.0 / 17.0 ms | **1 次 / 57 ms** |
| 关掉 blur + 插画底 | 16.0 / 17.0 ms | 0 次 |

结论按实测写，不按预期写：

1. **默认背景（CSS 光斑）下，玻璃在滚动期间没有可测代价**：四种配置的帧间隔逐档相同、
   长任务都是 0。同屏带 `backdrop-filter` 的元素实测 **4 个**。
2. **唯一出现长任务的场景是"模糊一幅有细节的插画底"**（1 次 57 ms），关掉 blur 即消失；
   而它**不可稳定复现**（同配置再跑一遍是 0 次）——更像首次合成的一次性开销，不是每帧成本。
   这条对应旧 §8.7 那条"大面积模糊"的老担心：它在**有插画**时才成立。
3. 于是模糊规则改写成三条可执行的上限（原规则是「遮罩默认纯色；模糊仅在 ≥960px 且非批量
   列表时启用」，已被本节取代）：
   - **(a) 同屏可模糊元素数 ≤ 12**（实测 4，留 3 倍余量）。只允许 `.surface-panel` /
     `.surface-main` / `.surface-sidebar` / `.id-tag` 与对话框外壳带模糊。
   - **(b) 时间线列表内的卡片只半透明、不模糊**。**必须说清：这一条是保守选择，不是实测
     结论**——上面第 4 行表明违反它也没测出代价。留它的理由是列表元素数随数据无上界，
     而唯一出现代价的那一档正是"模糊面积大 + 背景有细节"。**重新考虑的信号**：在窄屏或
     更大条目量下测出 `rule-broken` 与现状的帧率差。
   - **(c) 选了插画背景时单独重测**：那一档是唯一有长任务的，而 N 目前只按无插画的情况冻。
4. **背景插画有运行期入口**（用户在本机选一张图；图片**不进仓**，`texture_bytes` 因此仍是 0）。
   链路是「设置面板选图 → `features/settings/lib/image` 等比缩到 ≤1920px 并编成 JPEG(0.72)
   → 存进界面域 → `ui/glass/Backdrop` 渲染」。三条实现约束各自有来历：
   - **压过再存**：localStorage 配额通常 5 MB，手机截图/壁纸原始 data URL 会直接撞配额；
     压缩后一张壁纸通常 200–500 KB。超过上限（`BACKDROP_MAX_CHARS`）在写之前拒绝,
     不把失败留给 `persist` 在不确定的时刻抛。
   - **退化路径必须用 data URL，不能用 object URL**：CSP 的 `img-src` 限在 `'self' data:`，
     `blob:` 会被直接拦掉——那样"某个格式 `createImageBitmap` 解不开"时的兜底永远走不通
     （实测踩到：控制台报 CSP 违规，界面却显示成「这张图读不出来」，把真因藏了）。
   - **插画上叠一层 55% 的羊皮纸色平铺渐变**：那不是审美，是对比度的一部分——玻璃面是
     46% 半透明的，背后若是深色照片，面板会跟着变暗，而那套对比度是按"暖色浅底"验过的。
5. **这是 23b 唯一越过"承重层零改动"边界的地方**（`state/uiStore` 加一个偏好字段），
   由需求方在本阶段末尾显式批准。理由：入口本身必须有地方持久化，而界面域正是界面偏好的
   归属；它丢了不影响正确性。**没有**因此动 `api/` / `events/` / `lib/`。
4. 仍然禁止：SVG 滤镜（`feTurbulence` / `feDisplacementMap`）、位图纹理进仓、`transition-all`。
   `prefers-reduced-transparency` 不是本标准的要求（Chromium 只实现了 `reduce-motion`）；
   真正需要的是 §8.7 的 `reduce-motion` 归零。

**视觉回归**（阶段 23b 起有基线：`web/e2e/visual.spec.ts` + `web/e2e/visual.spec.ts-snapshots/`）：

- **按表面截图，不截整页**。整页会把导航列带进来，而它的内容是"这套 e2e 跑到现在攒下的
  所有会话"——跑第二遍就不一样，基线会天天报红。表面截图只取决于该用例自己造的数据。
  覆盖四种表面：会话落点页、对话卡（时间线 + 输入条）、审批待决、技能目录面板。
  （原稿的第五张是任务板面板，随任务图在阶段 27 一起删除。）
- **时钟必须在 `page.goto` 之前冻结**，且时刻要**相对数据**取。审批卡那句"还有 30 秒"是
  `expiresAt - Date.now()`：导航之后再冻只冻住一个已经算好的值（秒数继续跳）；冻在绝对
  常数上则差值随真实时钟漂移（差的是"几小时前"的那个小时数）。两条都是实测踩出来的。
- **ID 标签遮罩**（`.id-tag`）：会话/运行 id 是随机的，那是数据不是观感。
- **容差 ≤3 像素且单通道 ≤4**（`maxDiffPixels: 3`）。来历是 23a 实测的噪声下限：同一份
  构建在 5 个状态上各截两遍，其中 1 个状态出现 3 像素不同、单通道最大差 4/255，位置固定为
  导航列第一个条目按钮的 1px 左边框。写 0 会让同一份构建自己报红，门禁随即退化成
  "每次改动都点一下同意"。
- **报警已验证**：把 `--r-card` 从 18px 故意改成 30px → 落点页基线 409 像素不同、测试红；
  还原后恢复绿。稳定性：连跑 9 遍（含 20s 间隔）全部通过。

### 8.10 与 purrcat 的关系（已完成替换）

阶段 14 冻结的涂鸦语言是"照 purrcat 抄并 token 化"；阶段 23b 把它整体换掉。留下来的是
**与语言无关**的那部分：分层与门禁（L0–L4、`check:layers` / `check:tokens` / `check:style`）、
三域切分、单文件规模约束、Markdown 渲染映射、审批队列与分组窗口化的交互形状、i18n 的
单文件字典结构。

丢掉的是**语言本身**：九个机制、手写体、`--sketch-*` / `--sticker-*` / `--tilt-*` /
`--hatch-*` / `--paper-*` 全部 token 与类名，以及 33 条把取值钉死的断言。

一条可复用的教训：**`ui/` 之外的类名不含语言名**（23b 的判据 Ac9）——它让下一次换语言
只动 L0/L1。这条现在有机械守卫（`check:style` 的词表）。

## 9. 性能与体积

**判定口径**（沿用调研的三档：机制具备 / 有可测量数字 / 未验证）：本节给出的是**目标值与测量协议**，除「本机工具链版本」外没有任何实测数字，一律标 **未验证假设**。第一次测量前冻结阈值；测不到就写「未验证」，不用体积数字代替时延（这是调研最尖锐的两条教训：gemini-cli 的基线里两个滚动场景数值逐位相同、README 声明的 `eventLoopDelayP99Ms` 在数据里全部缺失，`dev/research/agent-frontend-survey-final.md:408`）。

| # | 目标 | 阈值 | 测量方式 |
|---|---|---|---|
| P1 | 首屏 JS（gzip） | **首次实测后冻结**（起点值：首屏 ≤450 KB、最大单块 ≤350 KB） | `pnpm -C web run gate:size`：读 `web/dist` 的 manifest，按入口闭包求和 |
| P2 | 分块预算豁免 | 豁免块必须在 `vite.config.ts` 的 `EXEMPT` 表里列名 + 写明原因；豁免块合计 ≤200 KB gzip | 同上；缺原因即失败 |
| P3 | 首屏可交互 | LCP ≤2.5s（本地静态 + 4× CPU 节流）；空会话首屏无 >200ms 长任务 | Playwright + Lighthouse；`PerformanceObserver({entryTypes:['longtask']})` |
| P4 | 首 token | 提交到首个可见 token ≤1.5s；**服务端 `request→first_delta` 单独记录，两个数字分开报** | `e2e/first-token.mjs`（Playwright + `MutationObserver` + 假 LLM） |
| P5 | 合并率 | 200 条 delta → commit 次数 ≤ 帧数（≤1 commit/帧） | React DevTools Profiler 或自建计数器；`coalescer` 单测直接断言 commit 次数 |
| P6 | 长会话 | 注入 1000 条条目：未虚拟化时 DOM 节点数 ≤800 且 p95 帧 ≤33ms；若引入虚拟化则 p95 ≤16.7ms | 注入脚本 + 滚动测量 |
| P7 | 重连不放大 | 退避 1s→30s（±30% 抖动）；10 分钟内重连次数有上界；重连不触发全量 entries 重取 | 断言 REST 调用次数（拦截统计） |
| P8 | 构建墙钟 | 只记录，不做门禁 | `time pnpm -C web build` |
| P9 | 字体、纹理与样式表字节 | 自托管字体 **0 B**（阶段 23b 弃用手写体）；`dist` 中位图纹理 0 个（favicon/logo 除外且 ≤32 KB）；**样式表 gzip ≤16 KB**（23b 新增，实测 5,630 B）；运行时对 `fonts.googleapis.com` 的请求 0 次 | `gate:size` 里一并统计 CSS / 字体 / 纹理；`grep -r fonts.googleapis web/dist` 无匹配 |

**新语言的成本结构**（与 P1–P9 的关系）：渐变背景与高光边都是纯 CSS，**没有位图纹理、没有 SVG 滤镜、不旋转任何东西**。代价集中在 `backdrop-filter`——实测（§8.9）：CSS 光斑底下它在滚动期间没有可测代价；唯一出现长任务的是「模糊一幅有细节的插画底」（1 次 57 ms，且不可稳定复现）。因此设三条上限：同屏可模糊元素数 ≤12、列表内不模糊、选了插画底时单独重测。详见 §8.9。

**P1 的起点值不是对标结论**：调研里明确警告不要沿用 OpenHands 的 450 KiB——那个常量只是通用 `vendor` 组的 `maxSize`（拆分阈值），且有两个被有意豁免并越线的块，仓库里没有产物体积门禁（`dev/research/agent-frontend-survey.md:522`、`dev/research/agent-frontend-survey-final.md:327-331`）。因此规则是：**门禁必须先存在，阈值必须来自 Avid 自己的首次测量**（记录在 `web/budget.json`，带 `frozen_at` 与测量环境），起点值只用来在第一次测量前拦住明显失控的引入。把「450 KB」当成达标依据是重复别人犯过的错。

预算与豁免的形态直接取自 OpenHands：预算常量 + **显式豁免表 + 豁免原因写成注释**，且承认它是「拆分阈值」不是「产物门禁」（`dev/research/agent-frontend-survey-final.md:327-331`）。Avid 的不同之处是：把豁免表做成 `gate:size` 会读的**数据**，所以豁免是显式的、可审计的，不是靠配置里恰好没写 `maxSize`。反面样本：Flowise 的 `vite.config` 里 `build` 段只有 `outDir`，实测最大单块 1.8 MiB gzip（`:299-308`）；Cline 主 webview 把 `chunkSizeWarningLimit` 设成 100000（≈关闭告警）而同一仓库的 hub 前端有真预算（`:343`）。

**测量纪律**（三条，缺一条数字就不可比）：① 单位写明（raw 还是 gzip）；② 同一场景唯一，禁止复制粘贴的重复条目；③ 文档与数据同步——基线文件的字段集合必须与测量脚本产出一致。这三条正是 gemini-cli 基线暴露的三个问题。

---

## 10. 失败与恢复

判据 §7 要求「每个模块回答它怎么失败」，逐类给出（此处按失败类型，而不是按模块，因为同一类失败在多个模块的处理必须一致）：

| 失败类型 | 表现 | 谁知道 | 重试 | 恢复 |
|---|---|---|---|---|
| 临时失败（模型 5xx、网络抖动） | `LLMError` 抛出 | svc → `run_failed{code:"llm_error"}` | **不自动重试**（当前内核行为，`client.py:156-161`）；用户可重新提交 | 已落库的消息保留；重新提交时从会话续接 |
| 永久失败（模型 401/403） | `LLMError` | 同上 | 否 | UI 提示检查配置（`config.py` 的报错文本已经包含变量名与修复命令） |
| 上下文超限 | `PromptTooLongError`（`client.py:24`） | 循环内部处理 | 兜底压缩后**重试一次**（`loop.py:150-162`） | 压缩事件可见（§7.3） |
| 工具失败 / 拒绝 | `tool_call_finished{status:"failed"}` | 回文本，循环继续 | 由模型决定 | transcript 合法，循环继续 |
| 业务拒绝（工具返回「错误：…」） | `tool_call_finished{status:"failed"}` | 模型与用户都能看到 | 由模型决定 | transcript 合法，循环继续 |
| 权限拒绝（硬拒绝） | `tool_call_denied{kind:"hard"}` | 同上 | 否；文案已说明「不要重试」 | 同上 |
| 权限拒绝（用户拒绝/超时） | `approval_resolved{decision:"deny"}` | 同上 | 模型被明确告知「不要重复提交」（`hooks.py:138-141`） | 同上 |
| 数据错误（会话文件损坏） | `SessionError` | svc → HTTP 500/409；列表路径跳过坏项（`cli.py:238-247` 同原则） | 否 | 人工介入 |
| 环境错误（`bash` 超时/越界） | 工具回文本 | 模型 | 模型自行换做法 | — |
| 依赖错误（模型端点不可达） | `LLMError` | svc | 否 | — |
| 程序错误（工具抛异常） | `execute_one` 捕获转文本（`execution.py:87-88`） | 模型 | 模型 | 循环不中断 |
| SSE 连接断开 | `EventSource` 触发重连 | 前端 | 是，带游标 | `Last-Event-ID` 补齐；缓冲外则 `resync` |
| 服务重启 | run 消失 | 前端（`GET /runs/{id}` 404） | 否 | 条目链尾 `truncated_tail` 标记 + 提示「上次运行中断」 |
| 审批未答复 | 超时 | 服务端 | 否 | 收敛为 `deny`（I6） |

**异步化的七个必备回答**（判据 §7 对任何 queue/event/stream/background job 的要求）：

1. **为什么不能同步**：一次运行是 30 秒到数分钟（多轮模型调用 + 工具 + 可能的 subagent 300s 预算，`subagent.py:37`），同步 HTTP 既会超时也无法增量观察。
2. **解决哪种约束**：长任务 + 增量观察 + 阻塞式人机交互（用户要在运行中途批准）。
3. **消费者能否延迟**：能。权威视图来自会话条目，事件流只是实时视图。
4. **能否重复**：能。durable 事件重放不产生副作用——副作用只发生在内核执行工具时，而重放只读缓冲。
5. **是否要求顺序**：是，`seq` 单调（I4）。
6. **消息是否要持久化**：缓冲**不持久化**（有界内存）；权威数据在会话 JSONL。所以「事件流丢失」的最坏后果是 UI 需要重建视图，不是数据丢失。
7. **积压/宕机/重复消费**：缓冲满 → 淘汰最旧 → 客户端拿到 `resync`（显式，不静默）；服务宕机 → run 终止，前端据条目链尾判断中断；重复消费 → 前端 reducer 按 `(run_id, seq)` 幂等去重。

---

## 11. 不变量与守护者

| # | 不变量 | 守护者（唯一） | 绕过路径检查 |
|---|---|---|---|
| I1 | 条目提交后不可变 | `session/` 存储层（既有） | Web 不新增写入路径 |
| I2 | 一条消息在会话里只出现一次 | `SessionRecorder`（既有） | §14 A11 断言 web/svc 不直接调 `append_message`/`commit` |
| I3 | 一个会话同时至多一个活动 run | `svc/runs.py` 注册表 | **仅进程内**；跨进程无互斥（已知缺口，见 §12） |
| I4 | 一个 run 内 durable 事件 `seq` 严格单调、不重复 | 单发射线程 + 计数器 | 只有 observer 一个调用点 |
| I5 | 不丢：durable 事件要么被重放、要么以 `resync` 显式告知 | `svc/runs.py` 缓冲与游标 | 缓冲淘汰必须走 `resync` 分支 |
| I6 | 未决审批的默认结局是拒绝 | `svc/approvals.py` | 超时/取消/断连/重启四条路径都收敛到 `deny` |
| I7 | 内核不 import Web 框架 | grep 门禁（§14 A1） | 依赖方向由 `web/` 单点持有 |
| I8 | 内核不 import 会话之外的东西来持久化（既有 I7） | `loop.py` 不认识 `session/` | 与既有一致 |
| I9 | **取消不丢已产生的消息、也不产生伪造的工具结果** | `loop.py` 的两个检查点 + `RunCancelled` | §7.4 否掉了「hook BLOCK 取消」方案正是为了守住这条 |
| I10 | 前端不复制内核判断（何时继续、何时问、何时停） | 组件层无重试/停止决策 | 渲染状态机不驱动调度（§3.5） |
| I11 | 服务端对象的身份由服务端生成 | 前端不在 durable 事件外发明 `entry_id`/`approval_id` | 乐观渲染用临时 id 并在 durable 到达时就地替换 |
| I12 | 乱序收敛：durable 事件渲染前 delta 必须 flush；终止事件渲染前 delta 必须 cancel | `events/reducer.ts` + `events/coalescer.ts` 两个出口 | 三个样本的同一约束（`dev/research/agent-frontend-survey-verified-addendum.md:280-283`） |
| I13 | 权威终止不在事件流里：终止判定以运行注册表 + 已提交条目为准 | 前端对账逻辑（静默 30s 或流结束即对账） | OpenHands 自陈的同一竞态与 30s 兜底（`dev/research/agent-frontend-survey-verified-addendum.md:442-443`） |
| I14 | 列表必须有界：任何返回条目的端点在服务端就有默认 `limit` 与硬上限 | `web/routes/sessions.py` + `session/` 的 `EntryQuery` | Langflow 的 34 MB/请求事故（`dev/research/agent-frontend-survey-addendum-verified.md:165`） |
| I15 | delta 不落盘 | 事件缓冲只在内存；`session/` 只收 durable 消息 | 决定重放语义（§5.2） |

---

## 12. 并发与一致性

**谁与谁可能同时操作**：

1. 多个浏览器标签页 → 同一会话：注册表互斥，第二个 `POST /runs` 得 409。
2. 多个会话并发：每会话一个工作线程 + 独立事件缓冲。**当前的 `_ASK_LOCK` 是模块级全局锁**（`permission.py:60`），会跨会话互相阻塞——Web 路径注入自己的 `ask` 后不再经过它（§7.2），这是必须的修复而不是优化。
3. subagent 线程池（最多 4，`subagent.py:39`）→ 同一 run 内最多 4 个待决审批，但工具在父 run 内是顺序执行的，因此父 run 自身至多 1 个未决审批，其余来自子 run。UI 按队列呈现。
4. CLI 与 Web 同时对同一工作区操作：两者共用 `.avid/sessions/`，**当前没有跨进程文件锁**（`runtime-architecture.md:426` 已记录该缺口）。

**一致性坐标声明**（判据 §8 要求「哪一种数据、在哪个时间窗口内、对谁一致」）：

| 数据 | 一致性 | 谁修 | 用户会看到什么中间状态 |
|---|---|---|---|
| 会话条目 | **强一致**（单写者 + 提交即落盘，`session/` 的 `MutationLine` 既有保证） | — | 无中间态：一条消息要么在文件里要么不在 |
| 实时视图（活动域） | **最终一致**，窗口 = 事件延迟 + 重连补齐时间 | 前端（游标重放 / resync） | 可能出现「已知历史 + 正在到达的步骤」并存；`run_status` 用服务端时钟的 `elapsed_ms` 避免跨机漂移（LibreChat 的做法，`dev/research/agent-frontend-stack-survey.md:274`） |
| 待决审批 | **强一致**（服务端唯一决策点） | 服务端（已决表 + 超时） | 第二个标签页可能晚一拍看到 `approval_resolved`；重复答复返回 `accepted:false` |
| 任务图 | ~~**强一致**（`TaskStore` 单入口 + `RLock`），**仅进程内**~~ — 阶段 27 已下线（对象与存储都不存在了） | — | 无 |

**为什么不做跨进程会话锁**：当前部署形态是「一个 CLI 或一个 Web 服务」，两者同时写同一会话不是真实场景。触发条件写明：出现「CLI 与 Web 同时运行且用户对同一会话各提交一次」的真实案例，或实现桌面壳（它会 spawn 一个 sidecar 服务，于是必然是两进程）。

---

## 13. 12 组判据的覆盖情况

| # | 检查点 | 相关 | 结论落在 | 未验证假设 |
|---|---|---|---|---|
| 1 | 变化优先 | ✓ | §1（含频率/代价表与优先级） | 各变化的真实频率是估计 |
| 2 | 具体先行 | ✓ | §2（8 个抽象的准入与删除测试） | — |
| 3 | 耦合 | ✓ | §3.1、§5.3、§6 | 生成式契约的维护成本在 Avid 规模下是外推 |
| 4 | 边界与决定权 | ✓ | §3.1、§6.1（写权限归属） | — |
| 5 | 数据所有权与状态生命周期 | ✓ | §3.5、§11、§12 | 条目树将来加分支/压缩条目时的迁移路径未设计 |
| 6 | 不变量 | ✓ | §11（I1–I11） | — |
| 7 | 失败与恢复 | ✓ | §10（含异步化七问） | 模型端点的失败分类依赖 `client.py` 现有的粗粒度映射 |
| 8 | 并发与一致性 | **部分** | §12 | 跨进程互斥未做（触发条件已写明）；4 个并行审批的 UI 未做压力验证 |
| 9 | 依赖与不可靠边界 | ✓ | §4.2、§5.1、§6.3 | FastAPI/pydantic 在 Avid 场景下的体积与启动开销未实测 |
| 10 | 边界代价与权衡 | ✓ | §4.1、§4.2（反事实测试）、§5.1（被否方案） | 规模 ×100 的结论是推理，不是实测 |
| 11 | 可逆性与决策强度 | ✓ | §4.3（交付形态可拆）、§6.3（生成器可延后） | 传输从 SSE 升格到 WS 的迁移成本未实做 |
| 12 | 运行与演进 | ✓ | §9（目标值 + 协议）、§16 | 全部性能目标值未实测（§9 已声明） |

---

## 14. 可据以验收该架构的标准

**读法**：判定命令里出现的脚本/测试文件是本设计要求新建的；「可验收」的标准是**脚本存在且判定为真**，不是「现在就能跑」。带「当前基线」的都是可以在今天的仓库上直接跑、其结果作为起点的。

### A. 架构边界（grep 与断言，不依赖运行）

| # | 标准 | 判定方式 | 当前基线 |
|---|---|---|---|
| A1 | 内核不依赖 Web 框架 | `grep -rn "fastapi\|pydantic\|starlette\|uvicorn" src/avid/{ai,runtime,policy,session,tools} --include=*.py` → 无匹配 | 无匹配（成立） |
| A2 | 只有 `web/` 认识 HTTP 框架 | `grep -rln "fastapi" src/avid --include=*.py` → 全部落在 `src/avid/web/` | 无匹配 |
| A3 | 循环未被改造成调度器 | `grep -c "trigger_hooks" src/avid/runtime/loop.py` → 2；`grep -rn "while \|for round" src/avid/svc src/avid/web` → 无匹配；`grep -c "on_message\|on_event" src/avid/runtime/loop.py` 只增观察点 | 2 / — |
| A4 | `svc` 不 import `web` | `grep -rn "avid.web\|from \.\.web" src/avid/svc` → 无匹配 | — |
| A5 | 第二个接线点不复制内核 | `svc/runs.py` 调 `agent_loop`，且不出现 `LLMError` 之外的重试逻辑：`grep -rn "LLMError" src/avid/svc` → 仅捕获与映射 | — |
| A6 | 事件名单点 | `grep -rn "\"tool_call_started\"\|\"run_finished\"" src/avid --include=*.py \| grep -v events.py` → 只出现在测试与 `web` 的映射表 | — |
| A7 | 两侧事件清单一致 | `uv run pytest tests/test_event_contract.py -q` → 1 passed；断言 `EVENT_TYPES` 集合 == 从 `web/src/events/types.ts` 解析出的成员集合 | — |
| A8 | 网络出口唯一、feature 不互相 import | `pnpm -C web run check:layers` → exit 0；规则：`src/**`（除 `src/api/**`）出现 `fetch(`/`EventSource(`/`new WebSocket(` 即失败；`features/a` import `features/b` 即失败 | — |
| A9 | 样式值单点、无裸元素 | `pnpm -C web run lint` → 对 `ui/tokens.css` 之外的 hex/rgb、`dark:` 修饰符、Tailwind 内建调色板类、裸 `<button>/<input>/<select>` 报错 | — |
| A10 | 观察点收敛 | `grep -rn "on_message" src/avid --include=*.py` → 只有 `loop.py` 定义、`recorder.py` 实现、`cli.py` 与 `svc/runs.py` 接线 | 4 个文件（一致） |
| A11 | 会话写入者唯一 | `grep -rn "append_message\|\.commit(" src/avid/web src/avid/svc` → `web` 无匹配；`svc` 只经 `SessionRecorder` | — |
| A12 | 前端不直连第三方 | `grep -rn "https\?://" web/src --include=*.ts --include=*.tsx \| grep -v "^web/src/api/"` → 无匹配（本地反面样本 purrcat 的市场页直连 GitHub） | — |

### B. 协议与行为（pytest / curl / Playwright）

| # | 标准 | 判定方式 |
|---|---|---|
| B1 | 事件可无 UI 订阅并断言 | `uv run pytest tests/test_run_events.py -q`：用固定 `chat` 序列跑一次 run，断言事件类型序列、durable 事件 `seq` 严格递增、delta 无 `seq` |
| B2 | 游标补齐不重不漏 | 同一 run 取 12 条 durable 事件，分别以 `after=0/5/11` 订阅，断言收到的 seq 集合恰为 `{k+1..12}` 且无重复 |
| B3 | 缓冲淘汰显式化 | 把缓冲上限设为 2，以 `after=0` 订阅 → 收到 `resync`（而非静默缺口） |
| B4 | 审批挂起-答复-resume | 脚本化一个会触发审批的工具：断言 run 在 `approval_requested` 处不继续、`GET /runs/{id}` 为 `awaiting_approval`；`POST` allow 后同一 run 继续并产生 `tool_call_finished`；`seq` 连续无洞 |
| B5 | 重复答复不二次批准 | 对同一 `approval_id` POST 两次 → 第二次 `{accepted:false}`；工具实际只执行一次（工具调用计数 + 事件计数双断言） |
| B6 | 超时失败关闭 | 把审批超时压到 0.1s → 事件为 `approval_resolved{decision:"deny", reason:"timeout"}`，且工具未执行 |
| B7 | 取消的粒度与不丢 | 取消后 run 以 `run_cancelled` 结束；断言会话条目数与取消前一致（不多不少），且不存在「被拒绝的工具调用」这类伪造条目 |
| B8 | delta 可丢不影响正确性 | `pnpm -C web test` 里 reducer 的单测：只喂 durable 的最终状态 == 混入任意 delta 后再中断的最终状态 |
| B9 | 流式与非流式等价 | `uv run pytest tests/test_llm.py -q -k stream_equivalence`：同一段 mock SSE 与同一份非流式 JSON 产出逐字段相等的 `Turn`（含 `tool_calls` 分片归并） |
| B10 | 未知 API 不回落到 SPA | `curl -s -o /dev/null -w '%{http_code} %{content_type}' localhost:8765/api/nope` → `404 application/json` |
| B11 | 一个会话一个 run | 对同一 session 并发两次 `POST /runs` → 一个 201 一个 409，且事件的 `run_id` 不混 |
| B12 | SessionRecorder 是唯一落库点（端到端） | `uv run pytest tests/test_session_integration.py -q`：断言每条 durable 消息事件都对应一次提交，且 `entry_id` 一致 |
| B13 | 事件类型不漂移（运行时侧） | `GET /api/meta` 的 `event_types` == `EVENT_TYPES`，且 `features` 表声明的能力与实际可用的端点一致 |
| B14 | 解析失败不静默丢弃 | 把一个 SSE 帧的 `data` 切成两半喂给前端的解析器 → 断言它保留半行缓冲、不丢帧；喂一个非法 JSON 帧 → 断言产生「视图待重建」标记并请求 `resync`，而不是跳过 |
| B15 | 权威终止对账 | 事件流在 `run_finished` 之前被掐断 → 断言客户端在 30s 内以 `GET /runs/{id}` + 条目对账并落到终态，且提示「事件流不完整，已重建」 |
| B16 | 列表有界 | `GET /sessions/{id}/entries` 不给 `limit` → 返回条数 ≤ 默认 `limit`；给 `limit=10000` → 返回条数 ≤ 硬上限，且响应里带 `next_cursor` |

### C. 界面、可访问性与性能（pnpm 脚本）

| # | 标准 | 判定方式 |
|---|---|---|
| C1 | 体积预算 + 显式豁免 | `pnpm -C web run gate:size`：阈值从 `web/budget.json` 读（首次实测后冻结，带 `frozen_at` 与环境说明）；未冻结时用起点值首屏 gzip ≤450 KB、单块 ≤350 KB；`EXEMPT` 每项必须有 `reason`，豁免合计 ≤200 KB gzip |
| C2 | 首屏可交互 | `pnpm -C web run perf:lcp`：LCP ≤2.5s（4× CPU 节流）；空会话首屏无 >200ms 长任务 |
| C3 | 首 token（两个数字分开） | `pnpm -C web run perf:first-token`：可见首 token ≤1.5s；同时输出服务端 `request→first_delta` 的毫秒数，两者分别记录 |
| C4 | 合并率 | `pnpm -C web test -- coalescer`：200 条 delta 的 commit 次数 ≤ 帧数；1000 条 delta 下无 >50ms 长任务 |
| C5 | 长会话（分组窗口化） | `pnpm -C web run perf:long-session`：注入 1000 条条目，断言首屏只渲染 ≤1 组（20 条）+ 尾部；碰顶一次只加载一组（用 `scrollTop` 重置前置操作验证 400ms 冷却生效）；插入历史后焦点条目的 `getBoundingClientRect().top` 位移 ≤2px（高度补偿）；p95 帧 ≤33ms |
| C6 | a11y 阻塞门禁 | `pnpm -C web run test:a11y`：axe 零 critical/serious；基线文件只允许条目减少（`RUN_A11Y_ASSERT=true`） |
| C7 | 视觉回归（涂鸦风下的稳定性） | `pnpm -C web run test:visual`：截图前 `await document.fonts.ready` + 注入 `transition/animation: none`；硬阴影与位移只用整数像素；全部 story 与基线一致；有 diff 必须显式更新基线并在提交信息里写明原因 |
| C8 | 键盘可用 | `pnpm -C web run test:keyboard`（Playwright）：仅用键盘完成「提交 → 批准 → 在检查器查看 diff」，且每步 `document.activeElement !== body` |
| C9 | 主题可替换、可缩放 | `pnpm -C web run test:theme`：切换 `--avid-text-scale` 四档后时间线字号、行高**与阴影档位**同步变化；`prefers-reduced-motion: reduce` 下位移/旋转/脉冲时长为 0ms；对比度脚本**按 alpha 合成**计算并通过（正文 ≥4.5:1，且 `--avid-ink-40` 不出现在 ≤12px 文本上） |
| C10 | 重连不放大 | `pnpm -C web run test:reconnect`：断开 3 次后断言退避序列落在 1s→30s（±30%）区间内，且期间 entries 全量重取次数 ≤1 |
| C11 | 无静默失败 | `pnpm -C web run lint`：`catch` 块为空或有 `/* noop */` 即失败；每个 `src/api` 请求必须有超时与 `AbortSignal` |
| C12 | 降级路径可用 | `pnpm -C web run test:degraded`：用 Playwright 阻断 `/events` 请求 → 断言 UI 自动切到轮询、顶部出现降级提示、运行仍能推进到终态 |
| C13 | delta 是订阅而非默认 | `pnpm -C web run test:deltas`：不带 `?deltas=1` 订阅时不产生任何 delta 帧；带上时收到 delta 且不影响最终状态（与 B8 的前端部分呼应） |
| C14 | 玻璃语言 token 化 | `pnpm -C web run lint`（`check-style` 规则 1–4、12）：颜色字面量只在 `tokens.css`；`shadow-[…]` / `z-[…]` / 内联 `borderRadius` / 内联 `fontFamily` / `dark:` / 内建调色板类 / **Tailwind 内建档位（`rounded-xl`、`shadow-lg` 这类）** 全部即失败。最后一条是 23c 补的：此前只禁了 `shadow-[…]` 与内联圆角，拦不住 `rounded-xl`，于是「圆角只能取 `--r-*`」当时是空话 |
| C15 | 圆角与投影不漂移 | 圆角只能取 `--r-chip/face/card/panel/pill`（`rounded-chip/face/card/panel/pill`），投影只能取 `--lift-1/2/3`；由 C14 的档位禁令 + 内联样式禁令共同守住。**形状轮换那条已删**：新语言没有这个机制，`e2e/messages.spec.ts` 反过来断言「所有消息卡片同形」 |
| C16 | 字体零外部请求、零自托管文件 | `grep -r "fonts.googleapis\|fonts.gstatic" web/dist web/src` 零命中；`gate:size` 的 `font_bytes` 现在是 **0**（手写体已弃用，字体只剩系统栈） |
| C17 | 无位图纹理 | `gate:size` 断言 `web/dist` 内 `*.png/jpg/webp/gif` 仅 favicon/logo，且合计 ≤32 KB；光斑背景必须是 CSS 渐变（参照物的整幅插画因此**不进仓**：要么不做，要么做成运行期选图，见 §8.9） |
| C18 | 模糊预算与 CSS 体积 | **结构那一半是机械的**（`check:style` 规则 13）：`backdrop-filter` 只允许出现在 `src/ui/glass.css`，且只允许落在 `.surface-panel` / `.surface-main` / `.surface-sidebar` / `.id-tag` 四个选择器上——**列表内的卡片（`.surface-card` / `.surface-chip`）不得带模糊**，组件里用 `backdrop-blur-*` 或内联 `backdropFilter` 也即失败（四条反例都实测会红）。**运行期那一半（同屏 ≤12）静态查不了**：它由实测的 4 个 + 3 倍余量支撑，见 §8.9。`gate:size` 另有 `css_gzip_bytes`——此前门禁**完全不量 CSS**，而视觉改动几乎全在 CSS 里（见 §9） |
| C19 | reduced-motion 全覆盖 | Playwright：以 `prefers-reduced-motion: reduce` 打开 → 所有 hover/active 位移、旋转过渡、`animate-pulse` 的时长为 0；颜色变化保留 |
| C20 | 桌面壳样式不影响 Web | 浏览器（非桌面壳）下 `document.elementFromPoint(x, 16)` 必须是页面内容而不是拖拽条；`-webkit-app-region` 只在 `html[data-shell="desktop"]` 下生效 |
| C21 | 装饰层不进无障碍树 | axe 通过；且断言手绘 blob/胶带/点阵容器均为 `aria-hidden="true"`，每个入口按钮的可访问名来自内部文字 |
| C22 | 声明即加载、层级有刻度 | `pnpm -C web run check:tokens`：`tokens.css` 里声明的每个字体族必须要么解析到 `assets/fonts` 下的文件、要么是显式系统栈（禁止「声明了 Inter 却从未加载」）；组件里出现任意值 `z-[…]` 即失败，模块层级只能取 `--z-*` 六档 |

**验收分层**：流式等价（B9）、性能（C1–C5）、a11y（C6）与降级（C12）这几类默认不进「改一行代码就跑一次」的回路——按 OpenHands 的 pytest markers 做法（`stress` 默认不跑，需显式 `-m stress`；`dev/research/agent-frontend-survey.md:728`）把它们标成 opt-in 目标，同时在提交前必须跑一次，否则「门禁存在」与「门禁被执行」又会脱钩。

**验收口径纪律**（三条，贯穿 A/B/C）：单位必须写明；同一场景只允许一条记录；基线文件的字段集合必须与测量脚本产出一致。

---

## 15. 实施顺序（每阶段可独立验收）

| 阶段 | 内容 | 独立验收 | 依赖 |
|---|---|---|---|
| **F0 事件层（无 UI）** | `runtime/events.py`、`on_event` 观察点、压缩事件、hook 转发、前端 `events/*` 纯函数 + 单测、两侧清单测试 | A6、A7、A10；B1、B2、B8 | 无 |
| **F1 服务骨架** | `svc/`（运行注册表、审批、会话读）、`web/`（路由、SSE 编帧、DTO、静态与 404 规则）、`avid web` 子命令、审批注入与取消 | A1–A5、A11、A12；B3–B7、B10–B16 | F0 |
| **F2 前端骨架** | `web/` 工程、`AppShell`（纸张画布 + 弹性对话卡）、涂鸦 token 与 sketch 构件、时间线 + 工具卡 + 审批卡、自托管手写体子集、Storybook、lint 与层禁令 | A8、A9；C6–C9、C11、C14–C21 | F1（可用 curl 造的假事件先做 UI） |
| **F3 流式与性能** | `ai/client.stream_completion`、delta 通道、合并器接入、性能门禁与基线 | B9；C1–C5、C10、C12、C13 | F2 |
| **F4 可选扩展** | 会话分支视图、run 作用域工具白名单、i18n、检查器里的 diff/文件视图 | 各自新增条目 | 真实需求出现 |

F0 的价值不依赖前端：它把「工具是否开始过」「压缩是否发生过」「审批被谁拒绝」变成可断言的事实。**如果只做一件事，做 F0。**

**实施进展（2026-09-18）**：F3 与 F4 均已落地，`features.deltas` 与 `features.branches`
都翻成 1（F1/F2 之后的两条 UI 修正——中档宽度改左侧抽屉、用户消息加一枚专属手绘标记
——属非阶段性改动，记在 `dev/plan/roadmap.md`）。两处与原方案的差异：

1. §7.5 说「不需要动循环：chat 是注入参数，svc 传入一个绑定了 per-run 回调的 wrapper
   即可」。这句话漏了一层：**同一个 `chat` 也被压缩摘要使用**，直接包装会让摘要文本经
   delta 推给前端。因此 `agent_loop` 多了一个 `summarize` 参数（默认 = `chat`），svc 把
   主轮次接流式、摘要接非流式。
2. §5.2 说重放缓冲是「最近 512 条 durable 事件」。delta 与 durable 共用同一条实时队列，
   而 `_trim` 原本按总条数淘汰——于是 delta 的**多少**会决定 durable 是否被挤出缓冲
   （I5 的语义被 delta 左右）。`_trim` 改成只数 durable。
3. §5.5 把「会话树的分支/fork 视图」列为不做，其重新评估信号（出现第一次真实的
   fork/重新生成需求）已触发；实现只用了既有的「分支 = 链尾值」+ 一个命名空间枚举，
   见 `docs/guide/web-ui.md` §3.1。

---

## 16. 适用条件与失效信号

- **成立条件**：单机、单用户、单工作区；一次运行一个会话；内核同步执行、工具同步；事件量级为每 run 数十到数百条；前端是浏览器。
- **不成立条件**：需要多用户或远程访问（要鉴权与工作区隔离）；需要跨进程恢复运行中的 run；需要服务端主动推送非活动会话的变更；需要真正的实时协作（多人同时看同一 run 并各自审批）；条目量级达到 10⁵ 且需要全文检索。
- **下一次变化最可能落在哪**（按可能性排序）：① 新工具与新事件类型 → `runtime/events.py` + 前端一个 pattern 组件，成本最低；② delta 接入后渲染与合并的调优 → `events/coalescer.ts`；③ 会话分支/fork 的 UI → 新的 feature + 条目树视图；④ 传输升格到 WS（桌面壳/IDE 插件）→ `web/sse.py` 与 `api/stream.ts`；⑤ 多用户 → 需要一轮独立设计（鉴权、工作区、CSRF、审计），当前架构不覆盖。
- **重新评估的信号**（逐条与上文对应）：缓冲淘汰频繁触发 `resync`；同源连接数成为真实瓶颈（同时运行会话 >5）；条目渲染开始需要虚拟化（注入 1000 条后 p95 帧 >33ms）；事件类型超过 25 个（升级到契约生成）；出现第二个前端表面（升级为独立 IDL）；出现第二条「错误文本」约定（给 `ToolOutcome` 加 `status`）；CLI 与 Web 并发写同一会话（加文件锁）。

---

## 17. 未验证假设清单

1. 全部性能目标值（§9 的 P1–P8）——未做任何实测；测量协议已给出，阈值待第一次测量前冻结。
2. SSE 在目标部署形态下的实际表现（代理、HTTP/1.1 连接数、心跳间隔）——未验证。
3. FastAPI + pydantic 的引入对启动时间与 wheel 体积的影响——未实测。
4. 「时间线是唯一滚动容器」在条目高度差异大时（超长 diff）的手感——未验证；失效信号是 p95 帧超阈值。
5. 三档断点的具体宽度是估算，不是从真实使用得来。
6. subagent 并行审批（最多 4 个）在 UI 上的队列呈现是否可理解——未做用户验证。
7. `tool_call_finished.status` 的文本前缀判定（`错误：`）是一次可接受的临时手段；给 `ToolOutcome` 加结构化字段是明确的替换路径。
8. 调研中所有样本的运行时指标（TTI/INP/首 token/帧率）在原文中即为空缺（`dev/research/agent-frontend-survey-final.md:512`），因此本文引用的样本做得好的地方都是**机制**，不是**数字**；同样地，本文的性能目标值是目标，不是对标结论。
9. 「delta 不落盘」的重放语义已在 §5.2 定死，但它对**续接**的影响未实测：非流式路径下，进程在流式过程中被杀，用户看到的是「等待中」而不是半截文本——这个体验是否可接受未验证。
10. 降级轮询路径（C12）的实际延迟与请求量未测；1s 起步的间隔是估算。
11. 事件流静默 30s 的兜底阈值（I13/B15）取自 OpenHands 的常量，在 Avid 的运行时长分布下是否合适未验证。
12. 服务端分页默认 `limit=100` / 硬上限 500（I14）是借鉴 Langflow 事故后的取值，Avid 的条目长度分布不同，可能需要调整。
13. `GET /api/sessions` 的 O(会话数 × 文件大小) 代价在 Web 首屏暴露后的真实耗时未测（§6.1）。
14. ~~手写体拉丁子集的最终体积与中西混排可接受度~~ **已了结**：阶段 23b 弃用了自托管手写体（理由见 §8.3），`font_bytes` 预算重冻为 0，这条不再有对象。
15. 中文手写体子集（§8.3 的 F4 路径）的字形数与体积是估算（300–600 字形 / 120–300 KB），未跑 `pyftsubset`。
16. 涂鸦风的 `resize-y` 检查器在触屏与键盘操作下的可用性未验证（原生 resize 手柄对手指与键盘都不友好）。
17. 硬阴影分档（1–16px）与 `--avid-text-scale` 联动后的层级观感未验证。
18. 视觉回归**已落地**：容差按实测冻成 `maxDiffPixels: 3`（同一份构建截两遍差 3 像素、单通道 ≤4），基线（5 种表面）已入库，并验证过「改一个 token 就报警」（§8.9）。**仍未做的**是把它接进 CI——e2e 仍不进 CI（N7），基线只在本地跑。
19. 时间线分组窗口化的四个阈值（20 条/组、150px 回位、400ms 冷却、50px 贴底）是从 purrcat 的实现抄来的，未在 Avid 的条目长度分布下验证；它们只影响手感，不改协议。
20. 输入框 ≤0.5deg 倾斜在中英混排长文本下的实际观感未验证——如果可读性被判定受影响，直接归零（这是一条纯视觉取舍，推翻成本为零）。
