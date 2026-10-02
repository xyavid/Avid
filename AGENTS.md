# Avid 协作约定

本文件是本仓库的协作约定，对每个在本仓库工作的 agent 生效。

## 1. 项目简介

Avid 是一个自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都由自己掌控，做到可替换、可调试、可度量。

- **核心用途**：先做通用内核，场景后接；用同一个内核承载编码、检索、业务流等不同任务。
- **目标**：改动任一模块（模型 / 工具 / 记忆 / 上下文策略）不需要动其它部分，且改动前后有可对比的评测数字。
- **验收基准**：参考场景 **R**（读取本地文件 + 计算）——首个工具与后续评测集都从它长出来。
- **技术栈**：内核 Python 3.12，环境与依赖管理用 `uv`，运行期依赖只有 `httpx`。前端在 `web/`（React 18 + Vite + pnpm + TypeScript），纸本视觉与渲染层自研。
- **现状**：模型调用 → 循环 → 9 个内置工具 + stdio MCP → 权限三轴预设（四级 deny 阶梯 + bwrap 沙箱 + 审计）→ hook 四事件 → 技能 → 上下文压缩 → 会话持久化 → 本地 Web 服务，端到端可用。**浏览器界面已随阶段 33 重建**（纸本视觉对话界面 + 会话/工作区管理 + 设置，分支 `refactor/web-hana-ui`），见 `docs/status/CAPABILITIES.md` §11。

本文件只写**跨阶段的稳定约定**；会随阶段变化的现状、数字与阶段账本各有归属：

| 想问什么 | 去哪 |
|---|---|
| 现在能做什么、不能做什么、瓶颈与未知 | `docs/status/CURRENT_STATE.md` |
| 每项能力的入口与证据 | `docs/status/CAPABILITIES.md` |
| 有什么数字、缺什么数字 | `docs/status/BENCHMARK.md` |
| 分层、依赖方向与门禁 | `docs/status/ARCHITECTURE.md` |
| 下一步往哪走 | `docs/status/ROADMAP.md` |
| 已完成阶段的目标 / 实现 / 产出 | `dev/plan/roadmap.md`（本地，不入库） |

### 1.1 常用命令

```bash
uv sync && uv sync --extra web   # 内核依赖 / 追加 Web 依赖（fastapi·uvicorn·pydantic）
uv run --env-file .env avid --agent "读 pyproject.toml，告诉我项目名"
uv run --env-file .env avid web --port 8765

uv run pytest                    # 默认不跑 stress 与 eval（见 pyproject 的 addopts）
uv run pytest -m stress          # 复杂度与长会话门禁
uv run --env-file .env pytest -q -m eval_smoke -s   # 评测冒烟（真模型，有成本）
uv run ruff check src/avid && uv run mypy           # 与 CI 同一套静态检查
seiso check                       # 文档规范（kind 映射与豁免见 seiso.toml；--preview 另有实验规则）

pnpm -C web install               # 前端依赖（pnpm 9）
pnpm -C web run verify            # 前端门禁：typecheck + vitest + build + gate:size（体积预算）
pnpm -C web run copy:dist         # 构建产物交付到 src/avid/web/static/（avid web 服务它）
```

评测仪器（真模型，不是门禁）：`uv run --env-file .env python -m benchmarks.run --smoke`，参数见 `benchmarks/README.md`。安装、配置项、CLI 全量参数与 Web 交付形态见 `README.md`。前端的对账门禁（wire/event 契约、模式词表、体积预算）已随阶段 33 收口恢复；e2e 尚未重建，界面验收走实机 CDP 脚本（`dev/evidence/`）。

### 1.2 数据流

CLI 与 Web 是**两个平级接线点**，内核不知道有几个调用方（`svc/`）：

```text
CLI  avid --agent / --session ─┐
Web  POST /api/sessions/{id}/runs ─┴─► svc/runs.RunRegistry（线程 + 事件重放缓冲）
                                       ▼
                         runtime/loop.agent_loop —— 只表达调度顺序
                           ├ ContextManager.compose()  装配 SYSTEM / tail 块，编排压缩
                           ├ chat() → Turn             正文 + tool_calls
                           └ execution.execute_batch()
                                ├ policy/action 归一化 → policy/engine 裁决（deny > ask > allow）
                                ├ policy/sandbox 按能力账本组装 bwrap argv
                                └ 工具 handler（tools/*，含 MCP 包装）
       on_message ─► SessionRecorder ─► <工作区>/.avid/sessions/*.jsonl（durable 真相）
       on_event   ─► RunRegistry 缓冲 ─► SSE ─► 浏览器消费方（React 前端 web/）
```

- **`on_message` 是消息的唯一出口**：循环不 import 会话层，落库与否由回调决定。
- **`on_event` 是步骤级事实的通道**，不是第二个消息通道；事件不写进会话 JSONL。
- 五步压缩阶梯在 `policy/compaction.py`，编排归 `ContextManager`。
- 完整数据流、状态所有权与依赖门禁见 `docs/status/ARCHITECTURE.md` §2–§4。

### 1.3 关键子系统

| 子系统 | 位置 | 职责 |
|---|---|---|
| 模型适配 | `ai/`：`transport` 退避重试、`protocol` 共享词表、`providers/{openai_compat,anthropic,gemini}`、`usage` 四家 usage 归一、`transcript` 独占消息写入 | 换模型只动这一层 |
| 循环与运行期 | `runtime/`：`loop` 只调度、`state.RunState` 一次运行的全部可变状态、`execution` 工具协议、`context_manager` 上下文装配与压缩编排、`events` 事件名单点、`hooks` 默认回调 | 一次运行的生命周期 |
| 策略层 | `policy/`：`action` 归一化与风险分类、`engine`（deny > ask > allow）、`rules` 四级阶梯、`sandbox` bwrap、`audit`、`permission` 唯一装配点、`compaction`、`modes` 三轴预设、`skills`、`todo` | 阈值、规则与文案的高频变化集中地 |
| 会话 | `session/`：条目树 + 值 + 分支 + 变更线，`memory` 与 `jsonl` 两后端共用一套一致性用例，`recorder` 是唯一写入者 | 磁盘上的会话真相 |
| 应用服务 | `svc/`：`runs` 运行注册表与重放缓冲、`approvals` 待决表、`sessions` 读视图、`workspaces`、`picker` | 内核的第二个调用方 |
| 传输适配 | `web/`：FastAPI 路由 + pydantic DTO + SSE 编帧 + 静态资源 | 线格式的唯一所有者 |
| 工具 | `tools/`：`registry` 单点声明、`files`/`shell`/`subagent`/`skill`/`web_search`/`mcp`、`validate` 参数校验 | 9 个内置工具 + 该工作区声明的 MCP 工具 |
| 工作区 | `workspaces.py` + `~/.avid/workspaces.json` | 用户级注册表（索引，非权威） |
| 前端 | `web/`（React 18 + Vite + pnpm）：`api/types.ts` 与 `events/types.ts` 契约种子、`styles/tokens.css` 纸本 token 层、`markdown/` 自研渲染、`surfaces/` 页面 | 浏览器侧全部代码；线格式契约由对账门禁钉住（`test_wire_contract.py` / `test_event_contract.py`），交付走 `copy:dist` 进 `src/avid/web/static/` |
| 评测仪器 | `benchmarks/`：21 条 case × 3 变体、五种判定器、轨迹落盘 | **不进 wheel**，产品代码反过来不许依赖它 |

### 1.4 入口点

| 入口 | 位置 |
|---|---|
| CLI | `src/avid/cli.py`：单轮 / `--agent` 循环 / `--session` 会话 / `avid workspace` / `avid web` |
| Web 服务 | `src/avid/web/app.py`（FastAPI）；接口面（端点 / 事件 / 信任边界）见 `docs/guide/web-ui.md` |
| 测试 | `tests/`，镜像 `src/avid/` 结构；`tests/test_web_boundaries.py` 是分层门禁 |
| 模块入口 | `src/avid/__main__.py`（`python -m avid`） |

### 1.5 常见改动落点

| 要改什么 | 动哪里 |
|---|---|
| 新增工具 | 在实现函数上挂 `@tool(...)`——`tools/registry.py` 是单点，其余表全部派生 |
| 新增模型协议 | `ai/providers/` 加一个 provider，对循环返回**同形** `Turn` |
| 新增一类上下文 | `ContextManager.register_source(kind, fn)` 一行 |
| 调阈值 / 规则 / 文案 | `policy/` |
| 加一个事件 | `runtime/events.py`（唯一单点）；同时在 `web/src/events/types.ts` 的 EVENTS 块里加同名成员——`tests/test_event_contract.py` 拦住两侧漂移 |
| 加一个界面 | `web/src/surfaces/` 加页面并在 `app/App.tsx` 挂路由；颜色 / 字号 / 圆角只取 `styles/tokens.css` 的 token，不写散档 |

## 2. 提交规范

自 `git init` 起，每完成一个可验证的小步就提交一次；同一阶段内不攒成一个大提交。

**格式**

```
<type>(<scope>): <简述>

<正文：为什么这样改>
```

- **type** 取 `feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf`。
- **scope** 写受影响的模块名，如 `loop`、`tools`、`context`、`eval`、`perm`。
- **简述**用中文祈使句，说明"做了什么"，不超过 50 字，结尾不加句号。
- **正文**只在"为什么"无法从 diff 看出时写。diff 已说明"是什么"，正文补原因、取舍与被否掉的方案。
- **粒度**一个提交一件事；提交后项目保持可运行。

**示例**

```
feat(loop): 支持多步工具调用与终止条件

单次往返覆盖不了参考场景 R 的多文件问题。循环上限设为可配置，
超限时归类为"未完成"而非抛错，便于评测区分能力不足与预算不足。
```

```
docs(plan): 冻结需求、非目标与阶段路线
```

```
fix(tools): 读取不存在文件时回传错误文本而非中断循环
```

## 3. 阶段执行流程

"阶段"指一轮事先与你商定的开发目标；划分、优先级与顺序都在对话中确定，不预先排期。**完整阶段**动工前先出图；阶段内部的细碎修改不走此流程。

### 第零步：澄清需求（grill-me）

你提出新的功能或新的开发阶段时，我先调用 `grill-me` skill 向你提问，澄清需求范围、验收标准与技术细节，**得到回答后才出图、动工**；一轮问不完时，等回答后依据已定的答案再问下一轮。

- **问题数量与复杂度相称**：改动涉及的模块越多、边界越不确定，问题越多；能自己从仓库里查到的事实（现有实现、命名、依赖关系）自己查，只把真正由你决定的取舍拿来提问，不凑数、也不漏问。
- **每条问题编号并给出推荐答案**，方便你只回「同意」或直接改写。
- **澄清结论是第一步「文件清单」与第三步「验收标准」的依据**：范围、边界、验收标准都在这一步定下来。

**细节修改**（小范围、不改模块边界）不走上述流程：提问控制在 **1–3 个**；若上下文已足以确定范围与验收标准，**可直接不提问**——但仍然是「先澄清（或明确判定无需澄清）、后动手」。

### 第一步：出图（动工前，一条消息给全）

1. **文件清单**：本阶段新增 / 修改 / 删除的每个文件，各附一句话职责。
2. **目录树**：本阶段完成后的完整目录结构，用文本树展示。
3. **架构图**：本阶段完成后的模块划分与数据流向，手写 SVG 落盘到 `dev/architecture/phase-<阶段号>-<短名>.svg`（无外部依赖，浏览器可直接打开），图中标注每条数据流携带的内容。该图属过程文档，只存本地、不入库。

模块边界、数据所有权、失败模型按 `docs/design/architecture-criteria.md` 推导；出图时用一句话说明每个边界的依据。

### 第二步：动工

出图之后直接开工，无需等待批准；清单被否掉时改图再动工。阶段内部的小修直接改，模块边界发生变化时同步更新本阶段 SVG。

### 第三步：收尾举证

代码完成后，按开工时商定的**验收标准逐条**给出证据：可执行的命令加上实际输出。
然后把该阶段追加进 `dev/plan/roadmap.md` 的「已完成阶段」，写清目标、实现方式与产出结果。

### 结束条件

该阶段每条验收标准都有证据、且你确认后，才进入下一阶段。

## 4. 文档归属

**正式文档**（项目说明、使用手册、API 文档、部署与配置、冻结后的设计）进 `docs/`，目录与命名见 `docs/README.md`。
**过程文档**（开发计划、需求草稿、进度跟踪、笔记、会议记录、阶段出图）留 `dev/`，只存本地、不入库。

`docs/` 走白名单：落在 `docs/` 根目录或未知子目录的新文件默认被忽略，正式文档必须放进已放行的四类目录。

**检索范围**：`dev/` 只存本地、不入库，其中 `dev/tmp/` 还放着参考项目的完整副本
（实测 5.8 GB / 4.3 万个文件）。全仓 `grep` / `rg` / `find` 会把它们一起扫进来，
既慢又噪声大——按需把范围限定到 `src/`、`tests/`、`docs/`、`skills/`。

## 5. 架构推导判据

设计判据单独成文：`docs/design/architecture-criteria.md`（12 组检查点）。

### 使用约束

- 给出架构结论前，逐条走完 `docs/design/architecture-criteria.md` 的 12 组检查点。
- 每个取舍写清具体机制与原因，用「解决了什么 / 牺牲了什么 / 在什么条件下成立 / 什么信号出现时重新考虑」替换「最佳实践」「行业标准」「更优雅」「更可扩展」这类结论词。
- 涉及不可逆决策时，先列出证据与实验再下结论：假设 → 原因 → 证据 → 实验 → 决策。
- 只分析与当前问题真正相关的检查点，不为凑齐条目而制造复杂度。

## 6. 测试约定

- **单元测试先写**：要写单元测试就在写实现之前写；实现完成后补的单元测试不算数、不写。
- **E2E 是默认测试手段**：复杂功能一律用 E2E 验证是否真的跑通，不用单元测试代替。E2E 末尾必须产出一个**可重复的产物**——同一条命令重跑得到同样结论，产物落在仓库里、可被他人直接打开检查。
- **隔离测试先列失败清单**：必须对某个系统做隔离测试时，先把「它可能怎么坏」逐条列全，再写代码。
- **全套 E2E 只在收尾跑**：开发期间只跑与本次改动直接相关的最小验证，全套 E2E 留到 §3 第三步「收尾举证」执行一次。
