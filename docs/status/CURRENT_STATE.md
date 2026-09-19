# Avid 当前状态

本文件**只回答九个问题**。每个结论都以仓库内可核对的事实为依据，证据用 `path:line`
或可执行命令给出；凭印象或设计意图得出的内容会显式标注为「未验证假设」。

**写作基线**：commit `1ece312`（工作区干净），内核 61 个 Python 文件 / 12,376 行，
`web/src` 8,347 行（本版未重测），`tests/` 44 个 Python 文件（41 个测试文件 + 3 个支撑文件）/
12,439 行，评测仪器 `benchmarks/` 14 个 Python 文件 / 1,581 行 + 12 条 case + 69 个 fixture 文件。

**证据分级**：

| 级别 | 含义 | 可否复核 |
|---|---|---|
| 仓库内 | `path:line` 或命令 + 输出 | 换台机器拉下来就能复核 |
| 本地记录 | `dev/` 下的过程文档（不入库） | 只能证明「当时记了这么一句」，**不能当事实引用** |
| 未验证假设 | 设计文档自己标了未验证，或本文推出的推断 | 需要一次实验才能变成结论 |

结论会随代码变化：**每次阶段收尾、或任一问的答案被实测推翻时改这里**，不要在文末追加补丁。
能力清单见 `CAPABILITIES.md`，数字见 `BENCHMARK.md`，结构见 `ARCHITECTURE.md`，方向见 `ROADMAP.md`。

---

## 1. Avid 是什么？

**一个自建的 agent 运行时（harness）**：模型调用、工具执行、多步循环、上下文与记忆、
权限、评测六层都是自己的代码，目标是可替换、可调试、可度量（`AGENTS.md:7-11`）。

它不是聊天机器人，不是某个业务应用，也不是模型层——项目的架构定位是**编排层**
（`dev/drafts/requirements.md:39`，本地记录）。具体形态：

| 维度 | 现状 | 证据 |
|---|---|---|
| 内核语言与包管理 | Python 3.12 + `uv`，`src/` 布局，运行期唯一依赖 `httpx>=0.27` | `pyproject.toml:1-6` |
| 前端 | TypeScript（React + Vite），独立 pnpm 工具链；产物复制进 `src/avid/web/static/` 随 wheel 分发，安装者不需要 Node | `pyproject.toml:11-14`、`web/package.json` |
| 交互形态 | `avid` CLI（单轮 / `--agent` 循环 / 会话 / `web`）+ 本地 Web 界面（FastAPI + SSE） | `src/avid/cli.py:85-140,317-333`、`README.md:29-57` |
| 规模 | 内核 61 文件 / 12,376 行；前端 8,347 行；测试 44 文件（41 测试 + 3 支撑）/ 12,439 行；评测仪器 14 文件 / 1,581 行 | `find src -name '*.py'`、`find web/src -name '*.ts*'`、`find benchmarks -name '*.py'` |
| 定位与验收 | 通用内核、场景后接；**验收只认参考场景 R**（读取本地文件 + 计算） | `AGENTS.md:9,11` |
| 使用者 | 单人、本地优先、单机运行 | `dev/drafts/requirements.md:29-31`，本地记录 |

与「用别人的框架」的分界不在功能多寡，而在**改动落点**：换模型只动 `ai/`，换阈值只动
`policy/`，换交互只动 `cli.py` 或 `web/`——这条边界由会失败的门禁钉住，而不是靠约定
（见 `ARCHITECTURE.md` §4）。

## 2. Avid 解决什么问题？

需求冻结时把问题分了三层，至今没变（`dev/drafts/requirements.md:9-14`，本地记录；
`AGENTS.md:7-11` 是它的入库版本）：

| 层 | 问题 | Avid 的答案 | 现在到什么程度 |
|---|---|---|---|
| 能力层 | 想把某类任务交给 agent 自动完成 | 一个能自主多步调用工具的循环 | 已跑通：8 轮上限、14 个工具、TODO 与任务图、subagent（§3） |
| 工程层 | 用别人的框架 = 黑盒，prompt / 上下文 / 重试 / 权限都改不动 | 每层都是自有代码，边界清晰、可替换 | 已跑通：分层 + `A1`–`A13` 门禁把边界变成会失败的断言（`ARCHITECTURE.md` §4） |
| 认知层 | 懂概念但没形成可运行整体，改动靠感觉 | 有 trace、有评测集、有基线，改动可度量 | **仪器已就位**：trace + `benchmarks/` 的评测集与基线（§3、`BENCHMARK.md` §9）；但首轮基线没有区分度，所以「改动可度量」目前是**能测**而不是**能判**（§6、§7） |

落到具体用户价值，目前真实成立的是这三条：

1. **在选定的工作区里干活**：会话归属某个工作区，文件类工具的相对路径基准、`bash` 的
   工作目录、`.avid/` 与 `.tasks/` 的落点都由该工作区决定（`src/avid/runtime/state.py:49-51`、
   `docs/design/workspace-permission.md:12`）。
2. **危险动作有裁决、有账本**：四层裁决 × 三档模式，同一次运行内同类操作只问一次，拒绝文案
   区分「永远不许」与「这次不行」（`src/avid/policy/permission.py:351-410`）。
3. **每一步都留痕可回放**：每条消息一次提交落进 JSONL，运行事件分三档，Web 端能重连补齐
   （`src/avid/session/__init__.py:1-21`、`src/avid/runtime/events.py:1-16`）。

第 2 节开头那句「可度量」现在**有仪器但还没有分辨力**：评测能跑、数字能落盘，但首轮 11 条
非会话 case 上三个变体全部通过——这是 §6 与 §7 的主题。

## 3. Avid 当前可以做什么？

一句话：**能在选定工作区里跑一个多步 Agent 会话，自主调用 14 个工具，带审批、TODO、
跨会话任务图、并行子 agent、技能与上下文压缩，从 CLI 或本地 Web 操作，全过程可回放；
并且能用一条命令把「能不能把任务做成」量成通过率、成本与轨迹（`benchmarks/`）。**

| 能力 | 关键事实 | 证据 |
|---|---|---|
| Agent 循环 | 轮数上限 8；Stop 被拦最多补 1 轮；两个取消检查点（每轮开始前、每批工具执行前）；未收敛抛 `RoundLimitExceeded` 而不返回半成品 | `src/avid/runtime/loop.py:41-44,184-186,250-272,274-295` |
| 工具调用协议 | 14 个工具；参数在 `execute_one` 里按**发给模型的那份 schema** 统一校验（required/type/enum/数值边界）；失败按参数错误 / 执行失败 / 业务拒绝三类给不同的下一步；工具失败不中断循环 | `src/avid/tools/__init__.py:45-80`、`src/avid/tools/validate.py`、`src/avid/runtime/loop.py:275-285` |
| 权限与审批 | 四层裁决（硬拒绝 → 危险命令 → 越界 → 常规规则）× 三档模式（`strict`/`workspace`/`system`）；硬拒绝不可覆盖；危险命令三档一律问一次（按规范化命令原文记账）、越界在严格/工作区档问一次（按绝对路径记账）；审批回调可注入（CLI 读 stdin、Web 走审批队列），subagent 与父运行**共用一本账本** | `src/avid/policy/permission.py:40-160,351-410`、`src/avid/runtime/state.py:46-55` |
| 工作区 | 用户级注册表 `~/.avid/workspaces.json`；id 由根目录派生（重复登记幂等、索引丢失不丢数据）；运行级工作区根取代模块全局；CLI/Web/前端都能选与新增 | `src/avid/workspaces.py:31-70,113-264`、`src/avid/svc/picker.py` |
| 会话持久化 | 新建 / 续接 / 列举 / 删除；条目树 + 值 + 分支 + 变更线；内存与 JSONL 两个后端共用一套一致性用例；每条消息一次提交；跨进程 flock + 短写回滚 + 末行残片自愈 | `src/avid/session/__init__.py:1-21,75-125`、`src/avid/session/jsonl.py` |
| 分支 | 分支只是「链尾是谁」的一个值；可从任一历史条目分叉；条目树只增不改 | `src/avid/session/values.py`、`docs/guide/web-ui.md:110-121` |
| 上下文压缩 | 五步阶梯（工具结果落盘 → 按条数裁剪 → 免费瘦身 → 摘要替换 → 模型报超限兜底），阈值集中在 `policy/compaction.py`；完整记录落盘到 `.avid/context/` 并回一句「用 read_file 读回」；自动压缩与兜底各最多一次 | `src/avid/policy/compaction.py:42-71,188-402`、`src/avid/runtime/context.py:64-80` |
| 计划与提醒 | `todo_write` 整份替换的清单；连续 3 轮未更新时注入提醒；同名同参工具第 3、5 次追加建议性提醒（只提醒不阻断） | `src/avid/runtime/state.py:34`、`src/avid/runtime/loop.py:188-198`、`src/avid/runtime/hooks.py:334-384` |
| 任务图（跨会话） | 6 个工具；3 状态（`pending`/`in_progress`/`completed`）、2 个动作（`claim`/`complete`）；`blockedBy` 表达依赖、`owner` 表达分工；落在 `.tasks/{id}.json`，id 形如 `task_1a2b3c4d`；完成时报告本次新解锁的下游 | `src/avid/tools/tasks.py:41-64,121-360`、`src/avid/tools/schemas.py:235-301` |
| 子 agent | `subagent` 一次最多 4 个子任务，并行执行后汇总；子运行结构上去掉 `subagent` 自己（`SUB_TOOLS`）；子 agent 看不到父对话，prompt 必须自包含 | `src/avid/tools/__init__.py:80-81`、`src/avid/tools/subagent.py`、`src/avid/tools/schemas.py:172-203` |
| 技能 | 目录下 3 个技能（`agent-builder` / `code-review` / `pdf`）；系统提示里只放 `name + description`，正文由 `load_skill` 按需读取；目录在运行开始时重新扫描 | `skills/`、`src/avid/policy/skills.py:65-111`、`src/avid/runtime/state.py:100-123` |
| 模型接入 | OpenAI 兼容 `/chat/completions` 直连 httpx（不套 SDK）；非流式 `chat_completion` 与流式 `stream_completion` 返回**同形**的 `Turn`；连接超时 10s / 读超时 60s；token 用量逐轮累加 | `src/avid/ai/client.py:22-35`、`src/avid/ai/client.py` 的 `stream_completion`、`src/avid/runtime/loop.py:228-237` |
| Web 与前端 | 22 个 HTTP 端点（会话 / 运行 / 审批 / 事件流 / 任务 / 技能 / 工作区 / 元信息）；18 类事件分三档（16 durable + `run_status` + `assistant_delta`）；时间线 / 任务板 / 技能目录 / 设置四个页面；前端 L0–L4 分层；`pnpm run verify` 串起 6 项检查（分层 / token / 样式 / 类型 / 单测 / 体积） | `src/avid/web/routes/*.py`、`src/avid/runtime/events.py:24-104`、`web/package.json` |
| CLI | `--agent` / `--yes` / `--permission` / `--workspace` / `--session` / `--new-session` / `--session-name` / `--list-sessions` / `--delete-session`；子命令 `web`、`workspace {add,list,remove,permission}` | `src/avid/cli.py:85-140,317-333` |
| 可观测 | 逐轮 trace 与 token 用量打到 stderr；事件流 + 运行注册表（重放缓冲 512 条、终态记录保留 600s / 最多 200 个 run）；心跳与兜底常量单点定义 | `src/avid/svc/runs.py:53-65`、`src/avid/runtime/events.py:108-118` |
| 评测与基准 | AvidBench v0.1：12 条只读 case × 3 个变体（bare / core / full）；5 种确定性判定器、无 LLM judge；指标全部从事件流派生；每次运行落 `result.json` / `trajectory.jsonl` / `answer.txt` | `benchmarks/README.md`、`benchmarks/avidbench/`、`docs/status/BENCHMARK.md` §9 |

逐项参数与端点清单见 `CAPABILITIES.md`——本节只到「能做什么」这一层。

## 4. Avid 当前不能做什么？

分三类：**声称拥有但没有机制的**、**设计上明确不做的**、**做到一半的**。

| 做不到 | 类别 | 证据 |
|---|---|---|
| 证明「这次改动变好了」 | 做到一半 | 仪器已建成（`benchmarks/`，首个基线见 `BENCHMARK.md` §9），但首轮 11 条非会话 case 上三个变体全部通过、**成功率没有区分度**，且没有跑过一次单变量对照——「有数字」还不等于「数字能回答改动的好坏」。缺什么、下一步做什么写在 `BENCHMARK.md` §9.4 与 `ROADMAP.md` §1 |
| 跨会话记忆（提炼 / 召回 / 遗忘） | 声称有、实际无 | `AGENTS.md:7` 把「记忆」列为自有层；`src/` 下只有会话条目树与任务图，没有任何提炼或召回模块 |
| 沙箱执行 | 设计上不做 | `bash` 以本进程权限执行，安全来自四层裁决 + 审批（`src/avid/tools/shell.py`、`src/avid/policy/permission.py`）；`docs/design/workspace-permission.md:298` 自认这条是「覆盖够用」而非隔离 |
| 多用户、鉴权、远程安全暴露 | 设计上不做 | `docs/guide/web-ui.md:69-92`：只有回环监听 + Host/Origin 白名单，**明文写着没有认证**，能连上端口的人就能建会话、跑命令 |
| 多 provider | 设计上不做 | 只有一条 OpenAI 兼容路径（`pyproject.toml:6` 唯一运行期依赖是 httpx）；需求里 D-03 明确「早期不做多 provider 抽象」 |
| 中断后恢复运行 | 设计上不做 | `docs/design/runtime-architecture.md:308-324` 的「不做」清单含崩溃恢复 / checkpoint / 重放；取消只保证不丢已产生的消息、不产生伪造工具结果 |
| 两个进程同时操作同一会话的运行 | 做到一半 | 会话**文件**有跨进程锁（`jsonl.py` 的 `<会话>.jsonl.lock`），但「一个会话同时至多一个活动 run」只在进程内成立（`frontend-architecture.md:816` 的 I3 自标「已知缺口」） |
| 会话的下一段 | 做到一半 | 压缩条目、usage 台账、operation 状态机、SQLite 后端均未做（`runtime-architecture.md:635-643`） |
| 任务图的下一段 | 做到一半 | 子 agent 的 owner 身份、任务工具进审批、跨进程互斥、`completed` 回退、给人看的**写**路径都没做——Web 任务板是只读的（`runtime-architecture.md:1201-1211`、`docs/guide/web-ui.md:43-67`） |
| 前端的一部分 | 做到一半 | 虚拟列表、subagent 子事件转发、a11y 与视觉回归用例、性能门禁与基线（C1–C5/C10/C12 无脚本）都没有（`frontend-architecture.md:12-30` 的 D10、§17 的 20 条未验证假设） |
| 大输出以外的 token/成本管理 | 做到一半 | 只有按轮累加的 `tokens` 计数与摘要调用（`src/avid/runtime/state.py:73-77`）；没有按会话/模型/时间的成本台账，也没有预算上限 |
| 自动重试 | 设计上不做 | 模型 4xx/5xx 一律 `LLMError` 上抛终止；唯一的重试是「上下文超限 → 兜底压缩 → 重试一次」（`runtime-architecture.md:308-324`、`loop.py:214-226`） |

## 5. 当前最可靠的能力是什么？

**会话持久化的「写入 — 读回 — 分支」契约。** 判定依据不是它写得多，而是它同时具备四种
互相独立的保障，这在仓内是唯一的：

| 保障 | 具体机制 | 证据 |
|---|---|---|
| 同一套一致性用例跑两个后端 | 内存与 JSONL 后端共用 `session_cases`，不是各写各的测试 | `tests/test_session_conformance.py`、`tests/session_cases.py` |
| 唯一写入者有门禁钉住 | `SessionRecorder` 是唯一写入者；A11 断言 `web/`、`svc/` 里不出现 `append_message` / `.commit(` | `tests/test_web_boundaries.py:157-162`、`session/__init__.py:8` |
| 失败路径有专门机制且有用例 | 跨进程 flock、`os.write` 短写回滚 + 长度校验、`_fsync_dir`、末行残片按原子重写自愈 | `src/avid/session/jsonl.py`、`tests/test_session_jsonl.py` |
| 读路径有量级门禁 | 200 个会话（每个 60 条）列表**一次都不 open 会话文件**且 < 1.5s；5000 条消息摘要 < 1.0s、重放 < 3.0s | `tests/test_stress.py:79-165` |

另有两处加固：公开门面被钉成 43 个名字（`tests/test_session_facade.py`，改它就是公开接口
变更），以及条目提交后不可变、条目树只增不改（`frontend-architecture.md:814` 的 I1）。

**这条结论的边界**（避免读成「会话层没问题」）：

- 它保证的是**数据不坏、能读回**，不保证语义正确——压缩后的历史是否仍让模型答对，属于 §6。
- 「同时至多一个活动 run」只在进程内成立（§4 已列）。
- 量级门禁是「不许退化」的上限（阈值留 10–30 倍余量，`tests/test_stress.py:6-11`），不是容量设计。

## 6. 当前最差的能力是什么？

**跨会话记忆（提炼 / 召回 / 遗忘）。** `AGENTS.md:7` 把「记忆」与模型、工具、循环、上下文、
权限、评测并列为自有层，而 `src/` 下**没有任何提炼或召回模块**——今天跨会话唯一发生的事是把
整条 transcript 投影回消息列表（`src/avid/session/projection.py` 的 `messages_for_branch`）。
首轮基线顺手量到了这一层的价值与缺口（`BENCHMARK.md` §9.3 第 3 条）：同一条任务，带会话历史
**1 轮 / 3,020 token** 答对；干净上下文 **5 轮 / 16,651 token**，且答错。

**评测从「一件东西都没有」变成「有仪器、没分辨力」**——它不再是第一名，但也没到能交付判断的
程度：

| 应有 | 现状 | 证据 |
|---|---|---|
| 评测集 | **有**：12 条只读 case，覆盖 6 类 | `benchmarks/cases/v0/`、`tests/test_bench_cases.py` |
| 通过率 / 效果基线 | **有**，但**没有区分度**：11 条非会话 case 上 bare/core/full 全部 11/11 | `BENCHMARK.md` §9.2 |
| 回归对比机制 | **机制有、实验没做**：多臂/多 commit 的结果都落盘、差值可算，没跑过单变量对照 | `BENCHMARK.md` §7「本轮落地对照」 |
| 性能预算 | 未冻结 | `web/budget.json` 的 `frozen_at` 仍是 `null`，注释自称「起点值」 |
| 效果类门禁 | marker 有（`-m eval` / `-m eval_smoke`），**按设计默认不跑** | `pyproject.toml` 的 `addopts` |

后果因此变了性质：以前是「**没有可证伪的方式**判断 prompt / 压缩阈值 / 循环策略这一整类改动
的好坏」，现在是「**有方式，但这套任务量不出差别**」——首轮基线里 TODO 提醒触发了 10 次、
压缩一次都没触发，三个臂结果完全相同（`BENCHMARK.md` §9.3）。工作规则「没有可对比的评测数字，
不动 prompt 与循环策略」（`dev/drafts/requirements.md:85-86`，本地记录）仍然只能靠自觉执行，
但它现在该挡的是「没量出差别就宣布机制有用」，而不是「任何改动都不许做」。

**次之**（一并记录，但明确排在后面）——几处**具体**的验证空洞，都可用一条命令证伪：

| 空洞 | 证据 |
|---|---|
| hook 回调**没有超时机制**，挂住的 hook 会永久挂住整个运行 | `grep -n timeout src/avid/runtime/hooks.py` → 零命中；而 hook 是外部可注入的扩展点（`HookRegistry` 可由调用方替换），失败关闭只覆盖「抛异常」，不覆盖「不返回」 |
| svc 任务只读视图的部分派生字段与错误分支零断言 | `grep -rn dependency_titles tests/` → 零命中；`task_not_found` / `task_corrupt` 两个错误码在 `tests/` 里也零命中（`src/avid/svc/tasks.py` 有这两条分支） |
| wire 层错误码有一部分从没被触发过 | `src/avid/svc/errors.py` 共 20 个 code，其中 `invalid_request`、`picker_failed`、`run_already_finished`、`task_not_found`、`task_corrupt`、`too_many_streams` 在 `tests/` 里零断言 |
| 子 agent 的可观察性 | 子运行不向父事件流转发事件（`frontend-architecture.md:12-30` 的 D10 把「subagent 子事件转发」列入不做），父运行里它只表现为一段文本结果，失败与耗时无法从事件流区分 |

这四条都不影响「主链路能用」，但都在**错误路径**上——而错误路径的可靠性正是「最可靠能力」
（§5）之外没有同等保障的地方。

## 7. 当前最大的技术瓶颈是什么？

**反馈闭环合上了，但分辨率不够——它还回答不了「改了之后是变好还是变坏」。**

需求把项目目标写成「可替换、可调试、可度量」（`AGENTS.md:7`）。「可替换」有分层 + `A1`–`A13`
门禁支撑；「可度量」这一半现在有了仪器（`benchmarks/`）与首个基线，但四件事都没解决：
首轮 11 条非会话 case 上三个变体全部通过（**天花板效应**）、36 次运行里压缩管线**一次都没触发**、
**没有跑过一次单变量对照**、机器与耗时口径未固定。这四条不解决，「可度量」就只是「能测」。
顺带说清另一条成功标准「能在 30 分钟内替换任一模块」**本身也没有任何演练或计时机制**——
门禁守的是「依赖方向不被写反」，不是「替换要多久」（`dev/drafts/requirements.md:18-22`，本地记录）。

它同时解释了另外两件事：项目报的进展只能报测试项数；性能类改动只能靠「复杂度没退化」
这种间接证据（`test_stress.py` 的三条门禁）而不是「快了多少、值不值」。

**次一级瓶颈**（还没撞上，但量级门禁已经标出了天花板）：会话读模型是「整文件解析 +
每会话尾部窗口」，运行注册表是进程内单实例（`src/avid/svc/runs.py:53-65`）。量级门禁把
200 会话 / 5000 条钉在 1.5s / 3s，这是**不许退化**的下限，不是扩容设计；一旦会话数或单会话
长度上到另一个量级，需要的是索引或换后端（SQLite 后端在未做清单里）。

**还有一条被刻意留着的边界**（不是缺陷，是取舍，写在这里避免被当成瓶颈误判）：权限判定
是命令行**文本正则**（15 条危险正则 / 14 类原因 + 敏感路径分量判定），因此可以被间接调用
绕过（变量拼接、脚本内再调用、写进文件后执行）。做成机制级隔离要引入沙箱，与「本地单人、
可调试、规则可读」是对立的取舍（`src/avid/policy/permission.py:45-112`、
`docs/design/workspace-permission.md:292-302`）。

## 8. 当前最大的产品未知是什么？

**这套仪器能不能区分好坏——也就是「任务要难到什么程度，机制才会显出差别」。** 首轮基线只回答到
「当前 11 条太容易」（三个变体全部 11/11，`BENCHMARK.md` §9.3）。更根本的第二问没变：这条
「通用内核 + 场景后接」的路线，是否真能承载第二类场景。

为什么它仍然是最大的未知：

- 参考场景 R 是项目**唯一**的验收基准（`AGENTS.md:11`）：现在有数字了（11/11），但三个变体
  一样好，意味着这个数字对「机制有没有用」不提供任何信息；
- 唯一使用者是作者本人（`dev/drafts/requirements.md:29-31`，本地记录），没有第二个使用者、
  没有真实任务的通过率；
- 三个后续场景（编码 / 检索 / 业务流）都还没有跑过，`dev/plan/roadmap.md` 里所有「产出结果」
  报的都是测试项数，**测试项数增长不能被当作能力增长**（本地记录，逐条编号见
  `dev/plan/roadmap.md:19-175`）。

**次之（有具体证据的产品风险）**：`strict` 档下每个受管动作都要问（`permission.py:128-134`
的 `MODE_LABELS`）。审批疲劳的真实后果是用户长期加 `--yes`，四层裁决就退化成「硬拒绝一层 +
其余全放行」——这三个指标（被拒比例 / `--yes` 使用率 / 一次运行问了几次）**现在有采集通道**：
AvidBench 的 `denials` 与 `approvals_requested` 从事件流派生（`benchmarks/avidbench/telemetry.py`），
但首轮基线用 `auto_approve=True` 跑，所以一个都没采到。阈值该定在哪、默认档该不该改，仍然
无法用数据判断。

## 9. 下一个版本只解决什么问题？

**先给仪器加分辨力，再谈机制。** 评测集与首个基线这一版已交付 4/5（逐条现状与证据见
`ROADMAP.md` §1），剩下的一条是「跑一次单变量对照」；而首轮基线暴露了更前置的问题：
**任务集太容易，成功率没有区分度**。所以下一个版本只做一件事，三选一，在对话中定：

1. **加难度分层**（L3–L5：多失败点、长上下文、深依赖）再比三个变体——只有先有区分度，
   后面所有消融才有意义；
2. **兑现第 4 条**：跑一次「改 prompt 或压缩阈值」的单变量对照，产出两组数字与差值；
3. **冻结阈值**：把首个基线值与 `web/budget.json` 的 `frozen_at` 一起写死。

其余一律不做：长期记忆、沙箱、多 provider、SQLite 后端、压缩条目、任务图下一段、
前端 a11y 与视觉回归、虚拟列表。

**已定的取舍**（不重新讨论）：评测用**真模型 + 固定模型版本**（`AVID_MODEL` 写进 `result.json`），
回放只留给以后的机制回归；本阶段没有建回放层，所以评测不进 CI 全量。验收标准不再在本文件
复制一份——权威副本在 `ROADMAP.md` §1（避免两处数字各写各的，见 `BENCHMARK.md` §4）。
