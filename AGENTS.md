# Avid 协作约定

本文件是本仓库的协作约定，对每个在本仓库工作的 agent 生效。

## 1. 项目简介

Avid 是一个自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限各层都由自己掌控，做到可替换、可调试。

- **核心用途**：先做通用内核，场景后接；用同一个内核承载编码、检索、业务流等不同任务。
- **目标**：改动任一模块（模型 / 工具 / 上下文策略）不需要动其它部分。
- **验收基准**：参考场景 **R**（读取本地文件 + 计算）——首个工具从它长出来。
- **技术栈**：内核 Python 3.12，`uv` 管理依赖，运行期依赖只有 `httpx`；前端在 `web/`（React 18 + Vite + pnpm + TypeScript）。
- **现状**：模型调用 → 循环 → 8 个内置工具 + stdio MCP → 权限轻量化（毁灭级命令双确认 + 凭据拒读 + 跨平台沙箱 + 审计）→ hook 四事件 → 技能 → 上下文压缩 → 会话持久化 → 本地 Web 服务，端到端可用；浏览器界面随阶段 33 重建（纸本视觉对话界面 + 会话/工作区管理 + 设置）。
- **阶段 35 重置**：包平铺到仓库根（`avid/`，无 src 层）；评测仪器（benchmarks）整体删除，评测另立阶段；docs 体系撤除，**代码与模块注释是唯一现状**。

**仓库现状问谁**：不问文档，问代码——每个模块的职责、边界与不变量写在模块 docstring 与注释里；跨包边界由 `tests/test_web_boundaries.py` 的门禁（A1–A13）钉住，前端契约由 `test_wire_contract.py` / `test_event_contract.py` 双侧钉住。

### 1.1 常用命令

```bash
uv sync && uv sync --extra web   # 内核依赖 / 追加 Web 依赖（fastapi·uvicorn·pydantic）
uv run --env-file .env avid --agent "读 pyproject.toml，告诉我项目名"
uv run --env-file .env avid web --port 8765

uv run pytest                    # stress 门禁默认不跑（见 pyproject 的 addopts）
uv run pytest -m stress          # 复杂度与长会话门禁
uv run ruff check avid tests && uv run mypy   # 与 CI 同一套静态检查

pnpm -C web install               # 前端依赖
pnpm -C web run verify            # 前端门禁：typecheck + vitest + build + gate:size（体积预算）
pnpm -C web run copy:dist         # 构建产物交付到 avid/web/static/（avid web 服务它）
```

**模型连接只认 BYOK 配置**：`~/.avid/models.json` + 0600 的
`~/.avid/secrets.json`，由界面「设置 → 模型」或手编文件维护（结构见
`avid/ai/byok.py` 模块注释）；未配置时运行报「还没有模型配置」。`.env`
（`--env-file`）只承载运行期开关。安装、配置项与
CLI 全量参数见 `README.md`。

### 1.2 数据流

CLI 与 Web 是**两个平级接线点**，内核不知道有几个调用方（`svc/`）：

```text
CLI  avid --agent / --session ─┐
Web  POST /api/sessions/{id}/runs ─┴─► svc/runs.RunRegistry（线程 + 事件重放缓冲）
                                       ▼
                         agent/loop.agent_loop —— 只表达调度顺序（subagent 递归用）
                           ├ ContextManager(context).compose()  装配 SYSTEM / tail 块，编排压缩
                           ├ chat() → Turn             正文 + tool_calls
                           └ execution.execute_batch()
                                ├ security/action 归一化 → security/engine 裁决（默认放行；毁灭级问一次）
                                ├ security/sandbox 按能力账本组装 bwrap argv
                                └ 工具 handler（agent/tools/*，含 MCP 包装）
       on_message ─► SessionRecorder ─► <工作区>/.avid/sessions/*.jsonl（durable 真相）
       on_event   ─► RunRegistry 缓冲 ─► SSE ─► 浏览器消费方（React 前端 web/）
```

- **`on_message` 是消息的唯一出口**：循环不 import 会话层，落库与否由回调决定。
- **`on_event` 是步骤级事实的通道**，不是第二个消息通道；事件不写进会话 JSONL。
- 五步压缩阶梯在 `agent/compaction.py`，编排归 `ContextManager`（`agent/context.py`）。
- 跨包依赖方向由 `tests/test_web_boundaries.py` 门禁钉住：循环与工具协议对策略层零运行时依赖（A13）。

### 1.3 关键子系统

| 子系统 | 位置 | 职责 |
|---|---|---|
| Agent 核心 | `agent/`：`run`（唯一循环）、`spec` 运行输入收口、`state.RunState` 全部可变状态、`context` 上下文装配（CONTEXT_MAP 声明表）、`compaction` 压缩通路（保留最近 N 轮 + 摘要）、`stop` 终止路径（StopReason）、`execution` 工具协议、`transcript` 消息唯一所有者、`events` 事件名单点、`hooks` 默认回调、`todo`/`prompt`/`skills` | 一次运行的生命周期与上下文策略 |
| 模型适配 | `providers/`：`{openai_compat,anthropic,responses}` 实现 + `__init__` 注册表、`transport` 退避重试、`protocol` 共享词表、`client`、`usage` 四家 usage 归一、`byok` 模型配置、`verify` 连通校验 | 换模型只动这一层 |
| 安全 | `security/`：`action` 归一化与风险分类、`engine`（默认直接跑；毁灭级问一次；凭据硬拒；full 跳过询问）、`sandbox` bwrap、`audit`、`permission` 唯一装配点、`userdirs` | 毁灭级名单、阈值与文案的高频变化集中地 |
| 会话 | `session/`：条目树 + 值 + 分支 + 变更线，`memory` 与 `jsonl` 两后端共用一套一致性用例，`recorder` 是唯一写入者 | 磁盘上的会话真相 |
| 应用服务 | `services/`：`runs` 运行注册表与重放缓冲、`approvals` 待决表、`sessions` 读视图、`workspaces`、`workspace_registry`、`picker` | 内核的第二个调用方 |
| 传输适配 | `web/`：FastAPI 路由 + pydantic DTO + SSE 编帧 + 静态资源 | 线格式的唯一所有者 |
| 工具 | `agent/tools/`：`registry` 单点声明、`files`/`shell`/`subagent`/`skill`/`mcp`、`validate` 参数校验 | 8 个内置工具 + 该工作区声明的 MCP 工具 |
| 工作区 | `workspaces.py` + `~/.avid/workspaces.json` | 用户级注册表（索引，非权威） |
| 前端 | `web/`（React 18 + Vite + pnpm）：`api/types.ts` 与 `events/types.ts` 契约种子、`styles/tokens.css` 纸本 token 层、`markdown/` 自研渲染、`surfaces/` 页面 | 浏览器侧全部代码；交付走 `copy:dist` 进 `avid/web/static/` |

### 1.4 入口点

| 入口 | 位置 |
|---|---|
| CLI | `avid/cli.py`：无参数进交互会话（`/compact`、`/rewind` 与 `/<技能名>`，解析单点在 `agent/commands.py`）；带问题为单轮 / `--agent` / `--session` / `avid workspace` / `avid web` |
| Web 服务 | `avid/web/app.py`（FastAPI）；接口面看 `avid/web/routes/` 与 `schemas.py` |
| 测试 | `tests/`，镜像 `avid/` 结构；`tests/test_web_boundaries.py` 是分层门禁 |
| 模块入口 | `avid/__main__.py`（`python -m avid`） |

### 1.5 常见改动落点

| 要改什么 | 动哪里 |
|---|---|
| 新增工具 | 在实现函数上挂 `@tool(...)`——`agent/tools/registry.py` 是单点，其余表全部派生 |
| 新增会话内命令 | `agent/commands.py` 的 COMMANDS 加名字 + 执行分支（CLI 与 Web 自动继承解析）|
| 新增模型协议 | `providers/` 加一个实现模块 + `__init__.py` 的 PROVIDERS 一条表项，对循环返回**同形** `Turn` |
| 新增一类上下文 | `ContextManager.register_source(kind, fn)` 一行（`agent/context.py`） |
| 调毁灭级名单 / 权限阈值 / 文案 | `security/`（名单在 `action.DENY_PATTERNS`，决策在 `engine.py`）；压缩阈值在 `agent/compaction.py` |
| 加一个事件 | `agent/events.py`（唯一单点）；同时在 `web/src/events/types.ts` 的 EVENTS 块里加同名成员——`tests/test_event_contract.py` 拦住两侧漂移 |
| 加一个界面 | `web/src/surfaces/` 加页面并在 `app/App.tsx` 挂路由；颜色 / 字号 / 圆角只取 `styles/tokens.css` 的 token，不写散档 |

## 2. 提交规范

自 `git init` 起，每完成一个可验证的小步就提交一次；同一阶段内不攒成一个大提交。

**格式**

```
<type>(<scope>): <简述>

<正文：为什么这样改>
```

- **type** 取 `feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf`。
- **scope** 写受影响的模块名，如 `loop`、`tools`、`context`、`perm`。
- **简述**用中文祈使句，说明"做了什么"，不超过 50 字，结尾不加句号。
- **正文**只在"为什么"无法从 diff 看出时写。diff 已说明"是什么"，正文补原因、取舍与被否掉的方案。
- **粒度**一个提交一件事；提交后项目保持可运行。

## 3. 阶段执行流程

"阶段"指一轮事先与你商定的开发目标；划分、优先级与顺序都在对话中确定，不预先排期。**完整阶段**动工前先出图；阶段内部的细碎修改不走此流程。

### 第零步：澄清需求（先讨论后动工）

你提出新的功能或新的开发阶段时，先向你提问，澄清需求范围、验收标准与技术细节，**得到回答后才出图、动工**；一轮问不完时，等回答后依据已定的答案再问下一轮。**文件的具体编排属于必讨论项**：目录结构、包内文件落点、命名，先摆方案再动手。

- **问题数量与复杂度相称**：改动涉及的模块越多、边界越不确定，问题越多；能自己从仓库里查到的事实（现有实现、命名、依赖关系）自己查，只把真正由你决定的取舍拿来提问，不凑数、也不漏问。
- **每条问题编号并给出推荐答案**，方便你只回「同意」或直接改写。
- **澄清结论是第一步「文件清单」与第三步「验收标准」的依据**：范围、边界、验收标准都在这一步定下来。

**细节修改**（小范围、不改模块边界）不走上述流程：提问控制在 **1–3 个**；若上下文已足以确定范围与验收标准，**可直接不提问**——但仍然是「先澄清（或明确判定无需澄清）、后动手」。

### 第一步：出图（动工前，一条消息给全）

1. **文件清单**：本阶段新增 / 修改 / 删除的每个文件，各附一句话职责。
2. **目录树**：本阶段完成后的完整目录结构，用文本树展示。
3. **架构图**：本阶段完成后的模块划分与数据流向，手写 SVG 落盘到 `dev/architecture/phase-<阶段号>-<短名>.svg`（无外部依赖，浏览器可直接打开），图中标注每条数据流携带的内容。该图属过程文档，只存本地、不入库。

每个取舍按 §5 的判据推导，出图时用一句话说明每个边界的依据。

### 第二步：动工

出图之后直接开工，无需等待批准；清单被否掉时改图再动工。阶段内部的小修直接改，模块边界发生变化时同步更新本阶段 SVG。

### 第三步：收尾举证

代码完成后，按开工时商定的**验收标准逐条**给出证据：可执行的命令加上实际输出。
然后把该阶段追加进 `dev/plan/roadmap.md` 的「已完成阶段」，写清目标、实现方式与产出结果。

### 结束条件

该阶段每条验收标准都有证据、且你确认后，才进入下一阶段。

## 4. 注释与文档纪律（阶段 35 起）

- **没有 docs/ 目录**：仓库内的 md 只有 `README.md`（安装 / 配置 / 运行）、`AGENTS.md`（本约定）与技能的 `SKILL.md`（运行期契约）。
- **代码与注释是唯一现状**：能力、边界、契约、阈值都写在代码与注释里；注释漂移按 bug 修。
- **注释纪律**：
  - 精简——普通代码不注释，代码能表达的不写；
  - 注释只写代码本身表达不了的**约束**：为什么这样做、不变量、外部契约、失败语义、边界条件；
  - 风格参照 `avid/runtime/loop.py`、`avid/ai/transcript.py` 的既有注释：短、具体、说约束不说过程；
  - 改代码必须同步改注释；发现注释说谎，当场修。
- **过程文档**（计划 / 诊断 / 阶段出图 / 会议记录）留 `dev/`，只存本地不入库；其中 `dev/tmp/` 存参考项目副本（约 5.8 GB / 4.3 万文件）——全仓 grep / find 把范围限定到 `avid/`、`tests/`、`web/src`，既快又不带噪声。

## 5. 架构推导判据

设计结论动工前走一遍这十二组检查点；只分析与当前问题真正相关的，不为凑齐条目制造复杂度。

1. **变化优先**——先列什么会变、多频繁，再谈结构；「总因同一原因一起改」的模块背后可能缺一个边界。
2. **具体先行**——从最简单可运行的实现开始；抽象准入要真实重复 / 真实变化 / 真实耦合三证其一；删掉抽象后更简单且边界不丢，就撤回。
3. **耦合**——每条依赖回答「用了什么、谁变谁跟着变」，指名耦合机制（结构 / 数据 / 控制 / 时间 / 生命周期…）；不用「松紧耦合」这类结论词。
4. **边界与决定权**——每个边界说清隔离了什么、谁说了算；总是一起改的模块该合，一个模块装互不相关的多种变化该拆。
5. **数据所有权**——每份数据指名谁创建 / 谁可改 / 谁可读 / 谁是权威 / 谁负责同步；每个 state 说清生命周期与持久化时机。
6. **不变量**——先列「永远不能破」的性质，再指派唯一守护层，并检查没有绕过路径。
7. **失败与恢复**——失败路径与正常路径同级设计；临时 / 永久 / 业务拒绝 / 权限拒绝 / 环境错误各有机制；异步化先回答为什么不能同步。
8. **并发**——设计期显式建模：谁与谁同时动什么、谁提供互斥、一致性在哪个窗口对谁成立。
9. **不可靠边界**——外部依赖逐个回答不可用时怎么办；远程调用按丢失 / 重复 / 延迟 / 中断建模，给超时、幂等与对账方案。
10. **边界代价**——先算代价再决定；方案比较至少两个；结论写「解决了什么 / 牺牲了什么 / 何时成立 / 什么信号出现时重审」。
11. **可逆性**——按撤销难度分配论证成本；难撤销者（数据模型、公开 API）要迁移方案与回滚点。
12. **运行与演进**——性能从工作负载推导；安全按信任边界处理；每个架构写明适用条件与失效信号，以及下一次变化最可能落在哪。

禁止用「最佳实践」「行业标准」「更优雅」代替具体机制与取舍。

## 6. 测试约定

- **单元测试先写**：要写单元测试就在写实现之前写；实现完成后补的单元测试不算数、不写。
- **E2E 是默认测试手段**：复杂功能一律用 E2E 验证是否真的跑通，不用单元测试代替。E2E 末尾必须产出一个**可重复的产物**——同一条命令重跑得到同样结论，产物落在仓库里、可被他人直接打开检查。
- **隔离测试先列失败清单**：必须对某个系统做隔离测试时，先把「它可能怎么坏」逐条列全，再写代码。
- **全套 E2E 只在收尾跑**：开发期间只跑与本次改动直接相关的最小验证，全套 E2E 留到 §3 第三步「收尾举证」执行一次。
