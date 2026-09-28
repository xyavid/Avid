# Avid 协作约定

本文件是本仓库的协作约定，对每个在本仓库工作的 agent 生效。

## 1. 项目简介

Avid 是一个自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都由自己掌控，做到可替换、可调试、可度量。

- **核心用途**：先做通用内核，场景后接；用同一个内核承载编码、检索、业务流等不同任务。
- **目标**：改动任一模块（模型 / 工具 / 记忆 / 上下文策略）不需要动其它部分，且改动前后有可对比的评测数字。
- **验收基准**：参考场景 **R**（读取本地文件 + 计算）——首个工具与后续评测集都从它长出来。
- **技术栈**：内核 Python 3.12，环境与依赖管理用 `uv`；前端 TypeScript（React + Vite），独立 pnpm 工具链，产物复制进 `src/avid/web/static/` 随 wheel 分发。
- **当前状态**：最小模型调用、Agent 循环、9 个工具（`bash` / `read_file` / `write_file` / `edit_file` / `glob` / `todo_write` / `subagent` / `load_skill` / `web_search`）、技能系统、上下文压缩管线、安全分层（三轴预设 + 四级 deny 阶梯 + bwrap 沙箱 + 审计）与 hook 扩展点、会话持久化（`session/`）、工作区（`workspaces.py`）均已跑通。项目目标见 `dev/plan/roadmap.md`。
- **Web 层（阶段 15）**：内核加 `runtime/events.py`（事件类型单点 + `on_event` 观察点）、审批注入（`RunState.ask`）与取消检查点；新增应用服务 `svc/`（运行注册表与重放缓冲、审批待决表、会话读）与传输适配 `web/`（FastAPI + SSE + 静态资源，`avid web` 子命令，FastAPI 在 `[project.optional-dependencies].web`）；前端 `web/` 按 L0–L4 分层（tokens/sketch → primitives/patterns → features → layouts → routes）。接口与页面见 `docs/guide/web-ui.md`，设计见 `docs/design/frontend-architecture.md`。
- **阶段 16（F3 流式 delta）**：`ai/client.stream_completion` 按 SSE 解析并返回与 `chat_completion` **同形**的 `Turn`（分片累加器是纯函数，B9 断言逐字段相等）；svc 的生产路径用 `streaming_chat` 把正文增量接到事件流上，delta 带 `seq=None`、不落盘、不重放，且**不占用 durable 的重放预算**；`agent_loop` 多一个 `summarize` 参数把「主轮次」与「压缩摘要」分开（否则摘要文本会混进 delta 流）。`features.deltas = 1`。
- **阶段 17（F4 分支视图）**：会话层加 `scan_values(namespace)` 与 `branch_names()`（分支只是「链尾是谁」的一个值，条目树只增不改）；svc/web 加分支列表、分叉与 `POST /runs {branch}`；前端新增 `features/branches`（选择器 + 从链尾分叉）与条目动作行的「从此处分支」，由 route 用 `branchSlot` 组合。`features.branches = 1`。
- **阶段 18（工作区 + 权限三态）**：`workspaces.py` 是用户级注册表（`~/.avid/workspaces.json`，id 由根目录派生，所以重复登记幂等、索引丢失不丢数据），会话 header 记录归属，运行级工作区根（`RunState.workspace_root`）取代模块全局，CLI/Web/前端都能选；权限从"一道审批规则"变成四层裁决（硬拒绝 → 危险命令 → 越界 → 常规规则）×三档模式（`strict` / `workspace` / `system`）：危险命令三种模式一律问一次（按规范化命令原文记账），越界在 `strict`/`workspace` 问一次（按绝对路径记账）、`system` 放行。规格与决策表见 `docs/design/workspace-permission.md`。导航列是**"工作区即文件夹"**（按工作区分组、可折叠、组内超出 5 条给「展开其余 N 个会话」、每行带相对时间），建会话 = 在某个文件夹上点 ＋，因此没有"新建会话 + 工作区下拉"这一对；右上角 🔍 是纯客户端的按会话名搜索（不匹配工作区名、不持久化）。**新增工作区**在界面上是导航列右上角的 ＋：服务端弹宿主机文件夹选择器（`AVID_PICKER_CMD`/tkinter/zenity/kdialog/Windows/osascript 依次探测），取消什么都不做，重复返回 409 并切到已有的那个。
- **阶段 22（上下文占用与 cache hit 台账）**：`ai/usage.py` 是唯一的 provider adapter，把四家写法归一成同一个 `Usage`（OpenAI 的 `prompt_tokens_details.cached_tokens`、DeepSeek 兼容的 `prompt_cache_hit_tokens`、Anthropic 的 `cache_read_input_tokens` / `cache_creation_input_tokens`、Gemini 的 `usageMetadata.cachedContentTokenCount`），流式与非流式两条解析路径共用它；`RunState.usage_report()` 是**唯一**的计算点（上下文占用 = 最近一轮真实 `prompt_tokens` / 窗口 / 占用率 / **三块字符占比分配**（系统提示词、工具定义、对话消息——前两块从不发给前端，只能由循环在发请求前记字符数）、缓存读写与命中率、压缩次数与压缩后读数——最后一项由压缩之后下一轮的真实读数回填，不做本地估算），每轮进 `run_status`、终态进 `run_finished`，并由 `SessionRecorder.record_usage()` 按**分支**落进会话值（`avid.usage`，JSONL 线格式与 `STORAGE_VERSION` 未动）；窗口来自 `AVID_CONTEXT_WINDOW` 或内置模型名小表（`ai/config.py`），查不到就不算占用率、可空字段一律显示「—」而不是 0。**没有新增事件类型**，`features.usage = 1`。两条顺序不变量：用量值在**宣告终态之前**落盘（客户端收到 `run_finished` 就会重取分支列表），`record.status` 的翻转与终态事件入缓冲在**同一段临界区**（否则订阅者会看到"已终态 + 缓冲里没有终态事件"而静默提前返回）。前端指示器在 `features/composer`，常驻输入条那一行、紧邻发送按钮左侧。规格与取舍见 `runtime-architecture.md` §20、`docs/guide/web-ui.md` §3.3。
- **阶段 26（安全分层）**：把"权限"拆成**三个正交参数**（`approval` / `sandbox` / `network`）与三个用户预设（`manual`＝沙箱内免问、危险与越界问人；`auto`＝同样的沙箱，改由确定性分类器裁决、判不准即拒；`full`＝不问不套沙箱），**`full ≠ default`** 由三重锁保证（CLI `--allow-full-access`、Web `full_access_ack`、注册表/DTO/CLI 都不接受它作默认）。策略层分为 `policy/action.py`（Tool Broker：归一化 / 目标识别 / 风险分类）、`policy/engine.py`（Policy Engine：deny > ask > allow）、`policy/rules.py`（ADMIN → SYSTEM → PROJECT → USER 四级 deny 阶梯 + 唯一的放松点 SYSTEM `[allow]`；仓库文件只能加严）、`policy/sandbox.py`（bwrap：只读系统 + 可写工作区 + 掩蔽宿主凭据 + `--unshare-net` + env 白名单 + 按能力账本挂载授予）、`policy/audit.py`（`~/.avid/audit/*.jsonl`，放行也记）、`policy/permission.py`（门面：`build_run_security` 是**唯一**装配点）。沙箱不可用时**不静默降级**（manual 逐个问、auto 失败关闭，三处可见）；审计写失败只计数不改裁决。E2E `benchmarks/sandbox_boundary/` 四个臂 × 14 条攻击性探针给出 17 条布尔结论（含"broker 看不见的越界写被只读挂载挡住"）。规格见 `docs/design/workspace-permission.md`，出图 `dev/architecture/phase-26-security-layers.svg`。
- **阶段 27（待办清单面板 + 任务图下线）**：两件事。① 新增「待办清单」面板：`features/conversation/components/TodoPanel.tsx` + `features/conversation/lib/todos.ts`（`latestTodos`），常驻在**输入条正上方**（`ConversationView` 底栏、`TodoPanel` 之上就是时间线），数据从会话条目里**最后一次** `todo_write` 的调用参数推导——零新接口、零新存储，刷新与重进会话看到的正好是这条分支上的计划；默认展开、可折叠（`uiStore.todoExpanded`，收起过一次就记住），没有清单时整块不渲染（连它自己那份间距也不留）。那次 `todo_write` 仍以工具卡留在时间线里（记的是"当时提交了什么"），面板显示的是"现在的计划"，两者不合并。② **任务图整体下线**：删掉 `create_task` / `update_task` / `can_start` / `claim_task` / `complete_task` / `get_task` 六个工具、`tools/tasks.py`（`TaskStore`）、`svc/tasks.py`、`web/routes/tasks.py`、`GET /api/tasks{,/{id}}`、前端 `features/tasks/**` 与 `/tasks` 页面及导航项，`features.tasks` 特性位与 `uiStore.taskFilter` 一并删除；工具数 15 → 9（`bash` / `read_file` / `write_file` / `edit_file` / `glob` / `todo_write` / `subagent` / `load_skill` / `web_search`）。理由是消费者只有那个只读页面，而"这次对话的计划"由 `todo_write` 承接、跨会话的依赖与分工没有真实使用证据。**旧数据 `<工作区根>/.tasks/` 不迁移、不删除**，只是不再被读——留着它比写一段迁移代码便宜，也便于日后反悔时把文件捡回来。规格与删除理由见 `docs/design/runtime-architecture.md` §17（该章保留为当时的实现记录，开头有下线横幅）。
- **阶段 29（ContextManager：上下文装配单点化）**：新增 `runtime/context_manager.py`，把「模型这一轮看到什么」收拢为**块装配**：`Block(kind, content, section)`——SYSTEM 落位（`instructions` / `environment` / `skill_catalog` / hook 注入）**首轮定型、运行内不变**（前缀缓存友好）；TAIL 落位（`plan` / `run_state`）每轮重渲染、挂请求末尾且**不落库**；对话本体仍是 `Transcript`。压缩编排自 `runtime/context.py` **整体并入**（装配与预算同属一个所有者），五步管线留在 `policy/compaction.py` 签名不变；`compose()`＝压缩+渲染，`render()`＝兜底压缩后重渲染。TODO 计划从「每 N 轮一条 `[提醒]` user 消息（落库、要靠 notice 类型与用户输入区分）」改为 tail 块常驻（模型每轮可见、不落库不发事件），沉默计数器（`rounds_since_todo` / `todo_reminder_after` / `TODO_REMINDER_AFTER_ROUNDS`）整个退役。随之删除：`state.system_prompt` / `skills.build_system_prompt` / `AGENT_INSTRUCTIONS` / `todo.build_reminder` / `hooks.context_inject_hook`（`UserPromptSubmit` 扩展点保留给用户 hook）；subagent 与 bench 的提示词走同一装配器（各自的 `SUB_SYSTEM` / `SYSTEM_PROMPT` 以 instructions 覆盖传入，bare 臂经 `ContextManager.system_prompt()` 取同源系统提示词），压缩摘要的 `SUMMARY_SYSTEM` 留在 `policy/compaction.py`（依赖方向优先于文案集中）。**新增一类上下文 = `register_source(kind, fn)` 一行**；本阶段只做现有来源（RAG / Memory / 身份 / 租户 / Artifact 不留占位），记账内部按 kind 细分（`ComposedRequest.parts`）、对外仍三块，前端零改动。`TODO_REMINDER` 事件常量保留作 wire 兼容（不再发射）。规格见 `docs/design/runtime-architecture.md` §21，出图 `dev/architecture/phase-29-context-manager.svg`（本地不入库）。
- **架构设计**：`docs/design/runtime-architecture.md`——分层解耦方案、与 pi 的异同、两阶段落地路径与可验证验收标准。**阶段 A（原地抽取）与阶段 B（分包为 `ai/` / `runtime/` / `policy/`）均已落地**：循环只剩调度，`loop.py` 与 `execution.py` 对策略层**零运行时依赖**（`runtime/` 其余三个文件各有明确理由——`context_manager.py` 装配上下文并编排压缩、`state.py` 持有运行期实例、`hooks.py` 注册默认回调；见 `runtime-architecture.md` §12 判据 9，由 `tests/test_web_boundaries.py` 的 A13 门禁钉住），`Transcript`（现在 `ai/`）独占消息写入、`RunState` 取代 3 个 contextvars、`ContextManager` 独占上下文装配与压缩编排（阶段 29 起）、`execution` 独占工具协议。**阶段 12 新增 `session/`（与 `ai/` 平级、零 avid 内部依赖）**：条目树 + 值 + 分支 + 变更线，内存与 JSONL 两个后端共用一套一致性用例；循环只多一个 `on_message` 观察点，`cli.py` 是唯一接线处（见设计文档 §16）。

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
既慢又噪声大——按需把范围限定到 `src/`、`tests/`、`web/src/`、`docs/`、`skills/`。

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
- **全套 E2E 只在收尾跑**：开发期间只跑与当前改动直接相关的最小验证，全套 E2E 留到 §3 第三步「收尾举证」执行一次。
