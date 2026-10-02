# Avid 当前状态

本文件**只回答九个问题**。每个结论都以仓库内可核对的事实为依据，证据用 `path:line`
或可执行命令给出；凭印象或设计意图得出的内容会显式标注为「未验证假设」。

**写作基线**：分支 `refactor/web-hana-ui`（阶段 33 前端重建 + 收口）；内核 81 个 Python
文件 / 16,983 行，评测仪器 `benchmarks/` 20 个 Python 文件 / 3,668 行 + 21 条 case +
225 个 fixture 文件。前端随阶段 33 从零重建（`b1c54cb` 清掉旧前端之后动工），
`web/src` 58 个 TS/TSX/CSS 文件 / 8,478 行；`tests/` 契约测试已恢复，现为 60 个 Python
文件 / 18,018 行。

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
权限、评测六层都是自己的代码，目标是可替换、可调试、可度量（`AGENTS.md §1`）。

它不是聊天机器人，不是某个业务应用，也不是模型层——项目的架构定位是**编排层**
（`dev/drafts/requirements.md:39`，本地记录）。具体形态：

| 维度 | 现状 | 证据 |
|---|---|---|
| 内核语言与包管理 | Python 3.12 + `uv`，`src/` 布局，运行期唯一依赖 `httpx>=0.27` | `pyproject.toml:1-6` |
| 前端 | 阶段 33 重建：React 18 + Vite + pnpm（`web/`），纸本视觉 + 自研 markdown/高亮渲染；契约种子与对账门禁、体积门禁已恢复 | `web/src/`、`CAPABILITIES.md` §11 |
| 交互形态 | `avid` CLI（单轮 / `--agent` 循环 / 会话 / `web`）+ 本地 Web 服务（FastAPI + SSE + 托管前端产物） | `src/avid/cli.py:85-140,317-333`、`README.md:29-57` |
| 规模 | 内核 81 文件 / 16,983 行；前端 `web/src` 58 文件 / 8,478 行；测试 60 文件 / 18,018 行；评测仪器 20 文件 / 3,668 行 | `find src -name '*.py'`、`find tests -name '*.py'`、`find web/src -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.css' \)`、`find benchmarks -name '*.py'` |
| 定位与验收 | 通用内核、场景后接；**验收只认参考场景 R**（读取本地文件 + 计算） | `AGENTS.md §1` |
| 使用者 | 单人、本地优先、单机运行 | `dev/drafts/requirements.md:29-31`，本地记录 |

与「用别人的框架」的分界不在功能多寡，而在**改动落点**：换模型只动 `ai/`，换阈值只动
`policy/`，换交互只动 `cli.py` 或 `web/`——这条边界由会失败的门禁钉住，而不是靠约定
（见 `ARCHITECTURE.md` §4）。

## 2. Avid 解决什么问题？

需求冻结时把问题分了三层，至今没变（`dev/drafts/requirements.md:9-14`，本地记录；
`AGENTS.md §1` 是它的入库版本）：

| 层 | 问题 | Avid 的答案 | 现在到什么程度 |
|---|---|---|---|
| 能力层 | 想把某类任务交给 agent 自动完成 | 一个能自主多步调用工具的循环 | 已跑通：没有轮数上限（跑到模型不再请求工具）、9 个工具、TODO 清单与提醒、subagent（§3） |
| 工程层 | 用别人的框架 = 黑盒，prompt / 上下文 / 重试 / 权限都改不动 | 每层都是自有代码，边界清晰、可替换 | 已跑通：分层 + `A1`–`A13` 门禁把边界变成会失败的断言（`ARCHITECTURE.md` §4） |
| 认知层 | 懂概念但没形成可运行整体，改动靠感觉 | 有 trace、有评测集、有基线，改动可度量 | **仪器已就位**：trace + `benchmarks/` 的评测集与基线（§3、`BENCHMARK.md` §9）；但首轮基线没有区分度，所以「改动可度量」目前是**能测**而不是**能判**（§6、§7） |

落到具体用户价值，目前真实成立的是这三条：

1. **在选定的工作区里干活**：会话归属某个工作区，文件类工具的相对路径基准、`bash` 的
   工作目录、`.avid/` 的落点都由该工作区决定（`src/avid/runtime/state.py:49-51`、
   `docs/design/workspace-permission.md:12`）。
2. **危险动作有裁决、有账本、有物理边界**：四级 deny 阶梯 × 三轴预设，同一次运行内同类操作只问一次，
   区分「永远不许」与「这次不行」（`src/avid/policy/permission.py:351-410`）。
3. **每一步都留痕可回放**：每条消息一次提交落进 JSONL，运行事件分三档，Web 端能重连补齐
   （`src/avid/session/__init__.py:1-21`、`src/avid/runtime/events.py:1-16`）。

第 2 节开头那句「可度量」现在**有仪器但还没有分辨力**：评测能跑、数字能落盘，但首轮 11 条
非会话 case 上三个变体全部通过——这是 §6 与 §7 的主题。

## 3. Avid 当前可以做什么？

一句话：**能在选定工作区里跑一个多步 Agent 会话，自主调用 9 个工具，带审批、TODO、
并行子 agent、技能与上下文压缩，从 CLI 或浏览器界面（`web/`，阶段 33 重建）操作，
全过程可回放；并且能用一条命令把「能不能把任务做成」量成通过率、成本与轨迹（`benchmarks/`）。**

| 能力 | 关键事实 | 证据 |
|---|---|---|
| Agent 循环 | **没有轮数上限**（也没有这个开关）：跑到模型不再请求工具为止；Stop 被拦最多补 1 轮；两个取消检查点（每轮开始前、每批工具执行前）；取消走 `RunCancelled`，不返回半成品 | `src/avid/runtime/loop.py`（`itertools.count(1)`） |
| 工具调用协议 | 9 个工具；参数在 `execute_one` 里按**发给模型的那份 schema** 统一校验（required/type/enum/数值边界）；失败按参数错误 / 执行失败 / 业务拒绝三类给不同的下一步；工具失败不中断循环 | `src/avid/tools/__init__.py:47-82`、`src/avid/tools/validate.py`、`src/avid/runtime/loop.py:275-285` |
| 安全分层（阶段 26） | 三轴（`approval`/`sandbox`/`network`）正交 × 三个预设（`manual`/`auto`/`full`）；deny > ask > allow 的四级阶梯（ADMIN → SYSTEM`~/.avid/policy.toml` → PROJECT `<ws>/.avid/policy.toml` → USER 账本，仓库文件只能加严）；bwrap 沙箱（只读系统 + 可写工作区 + 掩蔽宿主凭据 + 无出网 + 环境白名单 + 按能力授予挂载）；`full` 三重锁；审计落 `~/.avid/audit/`（放行也记）；沙箱不可用不静默降级 | `src/avid/policy/{modes,action,rules,engine,sandbox,classifier,audit,permission}.py`、`src/avid/tools/shell.py` |
| 工作区 | 用户级注册表 `~/.avid/workspaces.json`；id 由根目录派生（重复登记幂等、索引丢失不丢数据）；运行级工作区根取代模块全局；CLI 与 Web 接口都能选与新增（暂无界面，见下） | `src/avid/workspaces.py:31-70,113-264`、`src/avid/svc/picker.py` |
| 会话持久化 | 新建 / 续接 / 列举 / 删除；条目树 + 值 + 分支 + 变更线；内存与 JSONL 两个后端共用一套一致性用例；每条消息一次提交；跨进程 flock + 短写回滚 + 末行残片自愈 | `src/avid/session/__init__.py:1-21,75-125`、`src/avid/session/jsonl.py` |
| 分支 | 分支只是「链尾是谁」的一个值；可从任一历史条目分叉；条目树只增不改 | `src/avid/session/values.py`、`docs/guide/web-ui.md:110-121` |
| 上下文压缩 | 五步阶梯（工具结果落盘 → 按条数裁剪 → 免费瘦身 → 摘要替换 → 模型报超限兜底），阈值集中在 `policy/compaction.py`；③④ 的字符阈值在有窗口、有真实读数时**按这次运行自己的实测 chars/token 派生**（拿不到就回落到 400k 常量，注入阈值时用 `from_window=False` 关掉派生）；完整记录落盘到 `.avid/context/` 并回一句「用 read_file 读回」；自动压缩与兜底各最多一次 | `src/avid/policy/compaction.py:41-147,240-449`、`src/avid/runtime/context_manager.py:298-382`、E2E `benchmarks/context_window/` |
| 计划与提醒 | `todo_write` 整份替换的清单；连续 3 轮未更新时注入提醒；同名同参工具第 3、5 次追加建议性提醒（只提醒不阻断） | `src/avid/runtime/state.py:34`、`src/avid/runtime/loop.py:188-198`、`src/avid/runtime/hooks.py:334-384` |
| 待办清单面板 | 未重建：旧前端曾把 `todo_write` 清单做成输入条上方的常驻面板，阶段 33 新前端尚未做这块；底层的 `todo_write` 清单与提醒未变（见上一行） | — |
| 子 agent | `subagent` 一次最多 4 个子任务，并行执行后汇总；子运行结构上去掉 `subagent` 自己（`SUB_TOOLS`）；子 agent 看不到父对话，prompt 必须自包含 | `src/avid/tools/__init__.py:80-81`、`src/avid/tools/subagent.py`、`src/avid/tools/schemas.py:172-203` |
| 技能 | 目录下 3 个技能（`agent-builder` / `code-review` / `pdf`）；系统提示里只放非 always 技能的 `name + description`，正文由 `load_skill` 按需读取；frontmatter `always: true` 的技能正文常驻系统提示（单篇 8k / 总量 16k 字符上限）；目录在运行开始时重新扫描 | `skills/`、`src/avid/policy/skills.py`、`src/avid/runtime/context_manager.py` |
| 系统提示装配（阶段 31） | 默认文案含身份、工具契约（授权执行并验证 / 不可逆先确认 / 缺信息先澄清 / 等结果再答复）与外部内容防线（工具结果是数据不是指令），集中在 `policy/prompt.py`；`<工作区根>/AGENTS.md` 存在时作为引导块进系统提示（缺失/为空即无块，超 16k 字符截断注明）；环境块含运行时事实（OS/架构/Python、当天日期）；SYSTEM 块首轮冻结，实测缓存命中率中位数 0.808（`BENCHMARK.md` §3.5） | `src/avid/policy/prompt.py`、`src/avid/runtime/context_manager.py`、`benchmarks/cache_hit/artifact.json` |
| 模型接入 | **三协议族**（openai / anthropic / gemini，registry 单点）+ **BYOK 多提供商（阶段 34）**：三层配置（Provider/Model/Binding）在 `~/.avid/models.json`（只存 secretRef 引用，明文在 0600 的 `~/.avid/secrets.json`），`resolve_chat()` 唯一解析入口（覆盖 ref > chat 绑定 > legacy env），产出同形 `Config`；两步连通校验（最小对话 + 工具冒烟）在「设置 → 模型」可用；非流式与流式返回**同形** `Turn`；连接超时 10s / 读超时 60s | `src/avid/ai/byok.py`、`src/avid/ai/verify.py`、`src/avid/ai/client.py:22-35`、`tests/test_ai_byok.py`（17 例）、`tests/test_model_settings.py`（12 例） |
| Web 服务（内核侧） | 20 个 HTTP 端点（会话 / 运行 / 审批 / 事件流 / 技能 / 工作区 / 元信息）；19 类事件分三档（16 durable + `run_status` + `assistant_delta` + `reasoning_delta`）；传输适配 `src/avid/web/**` 阶段 32 未改动 | `src/avid/web/routes/*.py`、`src/avid/runtime/events.py:24-104` |
| 前端 | **阶段 33 重建**（分支 `refactor/web-hana-ui`，2026-10）：React 18 + Vite + pnpm，纸本视觉（暖纸 / 青夜 token 层）+ 自研 markdown 渲染与语法高亮；页面 = 对话（思考块 / 工具卡 / 动作行 / 用量卡 / 按运行换模型）、会话管理、工作区分组、设置、组件墙；契约种子（`api/types.ts`、`events/types.ts`）与对账门禁、体积门禁已恢复 | `CAPABILITIES.md` §11、`web/budget.json` |
| CLI | `--agent` / `--yes` / `--permission` / `--workspace` / `--session` / `--new-session` / `--session-name` / `--list-sessions` / `--delete-session`；子命令 `web`、`workspace {add,list,remove,permission}` | `src/avid/cli.py:85-140,317-333` |
| 用量台账（阶段 22） | `ai/usage.py` 把 OpenAI / DeepSeek 兼容 / Anthropic / Gemini 四种 usage 写法归一成同一形状（缓存读/写可空）；`RunState.usage_report()` 单点算上下文占用（最近一轮真实 `prompt_tokens` / 窗口 / 占用率）、缓存命中率与压缩读数；每轮进 `run_status`、终态进 `run_finished` 与 `GET /api/runs/{id}`，并**按分支落盘**进会话值（刷新 / 切会话 / 重启后可见）；窗口来自 `AVID_CONTEXT_WINDOW` 或内置模型名小表，查不到就不算占用率 | `src/avid/ai/usage.py`、`src/avid/runtime/state.py` 的 `usage_report`、`src/avid/svc/sessions.py:list_branches` |
| 可观测 | 逐轮 trace 与用量打到 stderr（含缓存读与命中率、有窗口时含占用率）；事件流 + 运行注册表（重放缓冲 512 条、终态记录保留 600s / 最多 200 个 run）；心跳与兜底常量单点定义 | `src/avid/cli.py:usage_suffix`、`src/avid/svc/runs.py:53-65`、`src/avid/runtime/events.py:108-118` |
| 评测与基准 | AvidBench：两套 suite 共 21 条只读 case（v0 12 条 / v1 9 条难度 case，tier 3–5）× bare / core / full 三臂；5 种确定性判定器、无 LLM judge；指标全部从事件流派生；`--suite` 选版本、`--context-chars` 注入阈值（写进 `overrides`）；每次运行落 `result.json` / `trajectory.jsonl` / `answer.txt` | `benchmarks/README.md`、`benchmarks/avidbench/`、`docs/status/BENCHMARK.md` §9–§10 |

逐项参数与端点清单见 `CAPABILITIES.md`——本节只到「能做什么」这一层。

## 4. Avid 当前不能做什么？

分三类：**声称拥有但没有机制的**、**设计上明确不做的**、**做到一半的**。

| 做不到 | 类别 | 证据 |
|---|---|---|
| 证明「这次改动变好了」 | 做到一半 | 仪器与两套基线都有（`BENCHMARK.md` §9 / §10），单变量对照也真跑过一次（同 commit 只改压缩阈值，§10.3）——但**对照量到的是抖动，不是机制**（45 次运行最大 transcript 4,657 字符 vs 阈值 400,000）；加难度分层（tier 3–5）同样没有区分度（三臂 9/9），因为 94 次工具调用里 72 次是 `bash`。下一步的证据指向可写任务 |
| 跨会话记忆（提炼 / 召回 / 遗忘） | 声称有、实际无 | `AGENTS.md §1` 把「记忆」列为自有层；`src/` 下只有会话条目树，没有任何提炼或召回模块 |
| 沙箱执行 | **已落地**（阶段 26）：`bash` 在 bwrap 里跑（只读系统、可写工作区、掩蔽凭据、`--unshare-net`、环境白名单），文件类工具仍由阶梯 + 路径校验守住 | `src/avid/policy/sandbox.py`、`src/avid/tools/shell.py`；E2E `benchmarks/sandbox_boundary/` |
| 多用户、鉴权、远程安全暴露 | 设计上不做 | `docs/guide/web-ui.md:69-92`：只有回环监听 + Host/Origin 白名单，**明文写着没有认证**，能连上端口的人就能建会话、跑命令 |
| 多 provider | **已落地**（阶段 34，BYOK） | `src/avid/ai/byok.py`：Provider/Model/Binding 三层配置 + secret 引用 + chat 槽解析；协议族仍只有三种（openai / anthropic / gemini，ollama 走 openai 兼容端点）——「只改配置接入新端点」成立，「任意协议」不成立；候选池 A 组的触发条件（必须接第二个端点）已出现并处理 |
| 中断后恢复运行 | 设计上不做 | `docs/design/runtime-architecture.md:308-324` 的「不做」清单含崩溃恢复 / checkpoint / 重放；取消只保证不丢已产生的消息、不产生伪造工具结果 |
| 两个进程同时操作同一会话的运行 | 做到一半 | 会话**文件**有跨进程锁（`jsonl.py` 的 `<会话>.jsonl.lock`），但「一个会话同时至多一个活动 run」只在进程内成立（`src/avid/svc/runs.py` 的注册表是进程内单实例） |
| 会话的下一段 | 做到一半 | 压缩条目、usage 台账、operation 状态机、SQLite 后端均未做（`runtime-architecture.md:635-643`） |
| 任务图 | **已下线**（阶段 27） | 六个工具、存储、只读接口与 `/tasks` 页面一并删除；旧 `<工作区根>/.tasks/` 不迁移、不删除，只是不再被读（`docs/design/runtime-architecture.md` §17） |
| 前端 | **重建完成，余项待做**（2026-10）：页面与对账门禁、体积门禁已落地；浏览器 e2e（旧 Playwright 套件已删）、a11y（axe）、视觉回归与分层 / token / 对比度门禁未重建 | `web/budget.json`、`CAPABILITIES.md` §11 |
| 大输出以外的 token/成本管理 | 做到一半 | 只有按轮累加的 `tokens` 计数与摘要调用（`src/avid/runtime/state.py:73-77`）；没有按会话/模型/时间的成本台账，也没有预算上限 |
| 自动重试 | 设计上不做 | 模型 4xx/5xx 一律 `LLMError` 上抛终止；唯一的重试是「上下文超限 → 兜底压缩 → 重试一次」（`runtime-architecture.md:308-324`、`loop.py:214-226`） |

## 5. 当前最可靠的能力是什么？

**会话持久化的「写入 — 读回 — 分支」契约。** 判定依据不是它写得多，而是它同时具备四种
互相独立的保障，这在仓内是唯一的：

| 保障 | 具体机制 | 证据 |
|---|---|---|
| 同一套一致性用例跑两个后端 | 内存与 JSONL 后端共用 `session_cases`，不是各写各的测试 | `tests/test_session_conformance.py`、`tests/session_cases.py` |
| 唯一写入者有门禁钉住 | `SessionRecorder` 是唯一写入者；A11 断言 `web/`、`svc/` 里不出现 `append_message` / `.commit(` | `tests/test_web_boundaries.py:151-153`、`session/__init__.py:8` |
| 失败路径有专门机制且有用例 | 跨进程 flock、`os.write` 短写回滚 + 长度校验、`_fsync_dir`、末行残片按原子重写自愈 | `src/avid/session/jsonl.py`、`tests/test_session_jsonl.py` |
| 读路径有量级门禁 | 200 个会话（每个 60 条）列表**一次都不 open 会话文件**且 < 1.5s；5000 条消息摘要 < 1.0s、重放 < 3.0s | `tests/test_stress.py:79-165` |

另有两处加固：公开门面被钉成 45 个名字（`tests/test_session_facade.py`，改它就是公开接口
变更），以及条目提交后不可变、条目树只增不改（`src/avid/session/types.py:40-53`）。

**这条结论的边界**（避免读成「会话层没问题」）：

- 它保证的是**数据不坏、能读回**，不保证语义正确——压缩后的历史是否仍让模型答对，属于 §6。
- 「同时至多一个活动 run」只在进程内成立（§4 已列）。
- 量级门禁是「不许退化」的上限（阈值留 10–30 倍余量，`tests/test_stress.py:6-11`），不是容量设计。

## 6. 当前最差的能力是什么？

**跨会话记忆（提炼 / 召回 / 遗忘）。** `AGENTS.md §1` 把「记忆」与模型、工具、循环、上下文、
权限、评测并列为自有层，而 `src/` 下**没有任何提炼或召回模块**——今天跨会话唯一发生的事是把
整条 transcript 投影回消息列表（`src/avid/session/projection.py` 的 `messages_for_branch`）。
首轮基线顺手量到了这一层的价值与缺口（`BENCHMARK.md` §9.3 第 3 条）：同一条任务，带会话历史
**1 轮 / 3,020 token** 答对；干净上下文 **5 轮 / 16,651 token**，且答错。

**评测从「一件东西都没有」变成「有仪器、没分辨力」**——它排不到第一名，但也没到能交付判断的
程度。逐条如下，**后两条是本轮（v1 加难度 + 一次对照）新量出来的**：

| 应有 | 现状 | 证据 |
|---|---|---|
| 评测集 | **有**：两套 suite 共 21 条只读 case（v0 12 / v1 9 条 tier 3–5） | `benchmarks/cases/`、`tests/test_bench_cases.py` |
| 通过率 / 效果基线 | **有**：v0 与 v1 各自的基线 | `BENCHMARK.md` §9.2 / §10.2 |
| 回归对比机制 | **有且真跑过**：同 suite、同 commit、只改 `--context-chars`，三档（§10.3） | `benchmarks/runs/v1-tight*/`（不入库） |
| 性能预算 | **已重冻**（阶段 33 收口） | `web/budget.json` + `gate:size` 按新前端实量冻结（首屏 JS gzip 70,192 B / 88 KiB、样式表 6,806 B / 16 KiB、字体 815 kB / 1 MiB、纹理 0），进 CI；旧冻结数字与新产物没有对应关系，已作废（`BENCHMARK.md` §3.2） |
| 效果类门禁 | marker 有（`-m eval` / `-m eval_smoke`），**按设计默认不跑**；另有一条 A14 边界门禁 | `pyproject.toml` 的 `addopts`、`tests/test_web_boundaries.py` |
| **区分度** | **仍然没有**：v1 的 9 条难度 case 上三臂 9/9；94 次工具调用里 72 次是 `bash` | `BENCHMARK.md` §10.2 |
| **压缩可评估性** | **不成立**：45 次运行最大 transcript 4,657 字符，而默认阈值 400,000——**差 86 倍**，阈值从未被触达 | `BENCHMARK.md` §10.3 |

后果因此第三次改变了性质：第一轮是「**没有可证伪的方式**判断 prompt / 压缩阈值 / 循环策略这类
改动的好坏」，第二轮是「**有方式，但任务量不出差别**」，现在是**知道差别的天花板在哪**——
只读任务的难度由「能不能一条 shell 折叠掉」决定，而压缩在这个上下文规模上根本不会被触发。
工作规则「没有可对比的评测数字，不动 prompt 与循环策略」（`dev/drafts/requirements.md:85-86`，
本地记录）现在有了一条具体的执行口径：**差异小于 full ±10% / core ±4% 就不要当结论**。

**次之**（一并记录，但明确排在后面）——几处**具体**的验证空洞，都可用一条命令证伪：

| 空洞 | 证据 |
|---|---|
| hook 回调**没有超时机制**，挂住的 hook 会永久挂住整个运行 | `grep -n timeout src/avid/runtime/hooks.py` → 零命中；而 hook 是外部可注入的扩展点（`HookRegistry` 可由调用方替换），失败关闭只覆盖「抛异常」，不覆盖「不返回」 |
| wire 层错误码有一部分从没被触发过 | `src/avid/svc/errors.py` 共 18 个 code，其中 `invalid_request`、`picker_failed`、`run_already_finished` 在 `tests/` 里零命中 |
| 子 agent 的可观察性 | 子运行不向父事件流转发事件（`src/avid/tools/subagent.py` 只把子任务的汇总文本交回父运行），父运行里它只表现为一段文本，失败与耗时无法从事件流区分 |

这三条都不影响「主链路能用」，但都在**错误路径**上——而错误路径的可靠性正是「最可靠能力」
（§5）之外没有同等保障的地方。

## 7. 当前最大的技术瓶颈是什么？

**反馈闭环合上了，分辨率仍然是瓶颈——这轮把"差多少才算差"量出来了。**

需求把项目目标写成「可替换、可调试、可度量」（`AGENTS.md §1`）。「可替换」有分层 + `A1`–`A13`
门禁支撑；「可度量」这一半现在有仪器、两套 suite 的基线、一次真跑过的单变量对照（`BENCHMARK.md`
§10.3）。**但这轮同时量出了它的分辨率上限**：同条件重跑的 token 抖动是
core ±3.7% / full ±8.5%；只读任务族的天花板是「一条 shell 折叠掉」（94 次工具调用里 72 次
`bash`）；压缩阈值要咬住得降到 ~4k 字符（45 次运行最大 transcript 4,657，默认阈值 400,000）。
**这三条不改变，"可度量"就只是"能测"，还不能"能判"。**
顺带说清另一条成功标准「能在 30 分钟内替换任一模块」**本身也没有任何演练或计时机制**——
门禁守的是「依赖方向不被写反」，不是「替换要多久」（`dev/drafts/requirements.md:18-22`，本地记录）。

它同时解释了另外两件事：项目报的进展只能报测试项数；性能类改动只能靠「复杂度没退化」
这种间接证据（`test_stress.py` 的三条门禁）而不是「快了多少、值不值」。

**次一级瓶颈**（还没撞上，但量级门禁已经标出了天花板）：会话读模型是「整文件解析 +
每会话尾部窗口」，运行注册表是进程内单实例（`src/avid/svc/runs.py:53-65`）。量级门禁把
200 会话 / 5000 条钉在 1.5s / 3s，这是**不许退化**的下限，不是扩容设计；一旦会话数或单会话
长度上到另一个量级，需要的是索引或换后端（SQLite 后端在未做清单里）。

**还有一条被刻意留着的边界**（不是缺陷，是取舍，写在这里避免被当成瓶颈误判）：Tool Broker
对 `bash` 的**目标识别**是启发式（扫字面量记号），所以变量拼接、脚本内再调用、解释器内构造
的路径都能让它看不见——但**看不见只影响「要不要问人」，不影响「能不能做到」**：真正拦住命令
的是沙箱的挂载与 network namespace（`docs/design/workspace-permission.md` §5；E2E 的
`invisible_outside_write` 就是这条的实测版：策略放行、只读挂载拦住）。

## 8. 当前最大的产品未知是什么？

**什么样的任务才能让 harness 的机制显出差别。** 这轮把答案推近了一步：**只读任务族不行**——
tier 3–5 的 9 条难度 case 上三臂仍然全过（`BENCHMARK.md` §10.2），因为 `bash` 一条命令就能折叠掉
「36 个文件」「40 跳链」这类结构；而压缩阈值要咬住需要 transcript 大两个数量级（§10.3）。
剩下两个方向都还没试：**可写任务**（改代码 + 跑测试：shell 折叠不掉，但需要 diff 采集与隔离）
与**大上下文任务**（长文档综合，让 400k 阈值真的被触达）。

更根本的第二问没变：这条「通用内核 + 场景后接」的路线，是否真能承载第二类场景。

为什么它仍然是最大的未知：

- 参考场景 R 是项目**唯一**的验收基准（`AGENTS.md §1`）：现在有数字了，但三个变体一样好，
  这个数字对「机制有没有用」不提供信息；
- 唯一使用者是作者本人（`dev/drafts/requirements.md:29-31`，本地记录），没有第二个使用者、
  没有真实任务的通过率；
- 三个后续场景（编码 / 检索 / 业务流）都还没有跑过，`dev/plan/roadmap.md` 里所有「产出结果」
  报的都是测试项数，**测试项数增长不能被当作能力增长**（本地记录，逐条编号见
  `dev/plan/roadmap.md:19-175`）。

**次之（有具体证据的产品风险）**：`auto` 档把 REVIEW 交给确定性分类器，而分类器只认规则
能表达的风险（`src/avid/policy/classifier.py`）：它判不准时会拒（失败关闭），但「拒得对不对」
只能靠 E2E 的探针表盯着。审批疲劳的真实后果是用户长期加 `--yes`，阶梯就退化成「deny 一层 +
其余全放行」——这三个指标（被拒比例 / `--yes` 使用率 / 一次运行问了几次）**现在有采集通道**：
AvidBench 的 `denials` 与 `approvals_requested` 从事件流派生（`benchmarks/avidbench/telemetry.py`），
但首轮基线用 `auto_approve=True` 跑，所以一个都没采到。阈值该定在哪、默认档该不该改，仍然
无法用数据判断。

## 9. 下一个版本只解决什么问题？

**做第一批可写任务（Basic coding 类），把"有没有区分度"这个问题一次问到底。**

证据来自本轮：只读族已被证明拉不开差距（`BENCHMARK.md` §10.2），而唯一被证明能拉开差距的维度
（跨会话投影）只有一条 case。可写任务需要的三件东西——workspace 写权限的判定口径、`final.diff`
采集、pytest 类确定性判定——都是**新的仪器组件**，与 case 内容一起做才不返工；上一轮把它们推迟
（`benchmarks/README.md` 的边界）是对的，但现在它们是唯一还有信息量的方向。

**同版不做**：压缩阈值的再校准（当前任务族不可评估，等可写或大上下文 case 进来再谈）、
长期记忆、多 provider、SQLite 后端、压缩条目、前端 e2e / a11y / 视觉回归（页面已重建，
这三类按重开信号另立项）、域名级网络授予（本机代理 + 白名单）。

**已定且不再讨论的取舍**：真模型 + 固定模型版本（`AVID_MODEL` 写进 `result.json`）；评测不进
CI 全量；**不设通过率门禁**（`BENCHMARK.md` §10.4）；case 集版本一旦有基线落盘就不再改。
验收标准与协议逐条现状在 `ROADMAP.md` §1，本文件不复制一份（避免两处数字各写各的）。
