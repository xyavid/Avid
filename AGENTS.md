# Avid 协作约定

本文件是本仓库的协作约定，对每个在本仓库工作的 agent 生效。

## 1. 项目简介

Avid 是一个自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都由自己掌控，做到可替换、可调试、可度量。

- **核心用途**：先做通用内核，场景后接；用同一个内核承载编码、检索、业务流等不同任务。
- **目标**：改动任一模块（模型 / 工具 / 记忆 / 上下文策略）不需要动其它部分，且改动前后有可对比的评测数字。
- **验收基准**：参考场景 **R**（读取本地文件 + 计算）——首个工具与后续评测集都从它长出来。
- **技术栈**：内核 Python 3.12，环境与依赖管理用 `uv`；前端 TypeScript（React + Vite），独立 pnpm 工具链，产物复制进 `src/avid/web/static/` 随 wheel 分发。
- **当前状态**：最小模型调用、Agent 循环、14 个工具（`bash` / `read_file` / `write_file` / `edit_file` / `glob` / `todo_write` / `create_task` / `update_task` / `can_start` / `claim_task` / `complete_task` / `get_task` / `subagent` / `load_skill`）、技能系统、上下文压缩管线、权限四层三态与 hook 扩展点、会话持久化（`session/`）、任务图（`tools/tasks.py`）、工作区（`workspaces.py`）均已跑通。项目目标见 `dev/plan/roadmap.md`。
- **Web 层（阶段 15）**：内核加 `runtime/events.py`（事件类型单点 + `on_event` 观察点）、审批注入（`RunState.ask`）与取消检查点；新增应用服务 `svc/`（运行注册表与重放缓冲、审批待决表、会话读、任务只读视图）与传输适配 `web/`（FastAPI + SSE + 静态资源，`avid web` 子命令，FastAPI 在 `[project.optional-dependencies].web`）；前端 `web/` 按 L0–L4 分层（tokens/sketch → primitives/patterns → features → layouts → routes）。接口与页面见 `docs/guide/web-ui.md`，设计见 `docs/design/frontend-architecture.md`。
- **阶段 16（F3 流式 delta）**：`ai/client.stream_completion` 按 SSE 解析并返回与 `chat_completion` **同形**的 `Turn`（分片累加器是纯函数，B9 断言逐字段相等）；svc 的生产路径用 `streaming_chat` 把正文增量接到事件流上，delta 带 `seq=None`、不落盘、不重放，且**不占用 durable 的重放预算**；`agent_loop` 多一个 `summarize` 参数把「主轮次」与「压缩摘要」分开（否则摘要文本会混进 delta 流）。`features.deltas = 1`。
- **阶段 17（F4 分支视图）**：会话层加 `scan_values(namespace)` 与 `branch_names()`（分支只是「链尾是谁」的一个值，条目树只增不改）；svc/web 加分支列表、分叉与 `POST /runs {branch}`；前端新增 `features/branches`（选择器 + 从链尾分叉）与条目动作行的「从此处分支」，由 route 用 `branchSlot` 组合。`features.branches = 1`。
- **阶段 18（工作区 + 权限三态）**：`workspaces.py` 是用户级注册表（`~/.avid/workspaces.json`，id 由根目录派生，所以重复登记幂等、索引丢失不丢数据），会话 header 记录归属，运行级工作区根（`RunState.workspace_root`）取代模块全局，CLI/Web/前端都能选；权限从"一道审批规则"变成四层裁决（硬拒绝 → 危险命令 → 越界 → 常规规则）×三档模式（`strict` / `workspace` / `system`），危险与越界与模式无关地要一次同意并按路径/命令记账。规格与决策表见 `docs/design/workspace-permission.md`。导航列是**"工作区即文件夹"**（按工作区分组、可折叠、组内超出 5 条给「展开其余 N 个会话」、每行带相对时间），建会话 = 在某个文件夹上点 ＋，因此没有"新建会话 + 工作区下拉"这一对；右上角 🔍 是纯客户端的按会话名搜索（不匹配工作区名、不持久化）。**新增工作区**在界面上是导航列右上角的 ＋：服务端弹宿主机文件夹选择器（`AVID_PICKER_CMD`/tkinter/zenity/kdialog/Windows/osascript 依次探测），取消什么都不做，重复返回 409 并切到已有的那个。
- **架构设计**：`docs/design/runtime-architecture.md`——分层解耦方案、与 pi 的异同、两阶段落地路径与可验证验收标准。**阶段 A（原地抽取）与阶段 B（分包为 `ai/` / `runtime/` / `policy/`）均已落地**：循环只剩调度且对策略层零依赖，`Transcript`（现在 `ai/`）独占消息写入、`RunState` 取代 3 个 contextvars、`context.prepare` 独占压缩编排、`execution` 独占工具协议。**阶段 12 新增 `session/`（与 `ai/` 平级、零 avid 内部依赖）**：条目树 + 值 + 分支 + 变更线，内存与 JSONL 两个后端共用一套一致性用例；循环只多一个 `on_message` 观察点，`cli.py` 是唯一接线处（见设计文档 §16）。

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

## 5. 架构推导判据

设计判据单独成文：`docs/design/architecture-criteria.md`（12 组检查点）。

### 使用约束

- 给出架构结论前，逐条走完 `docs/design/architecture-criteria.md` 的 12 组检查点。
- 每个取舍写清具体机制与原因，用「解决了什么 / 牺牲了什么 / 在什么条件下成立 / 什么信号出现时重新考虑」替换「最佳实践」「行业标准」「更优雅」「更可扩展」这类结论词。
- 涉及不可逆决策时，先列出证据与实验再下结论：假设 → 原因 → 证据 → 实验 → 决策。
- 只分析与当前问题真正相关的检查点，不为凑齐条目而制造复杂度。
