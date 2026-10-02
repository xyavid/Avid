# Avid 当前能力

本文件列**已经具备**的能力，以及每项能力的入口与证据。它回答「有什么、怎么用、在哪实现」，
不回答「好不好、缺什么」——那两问在 `CURRENT_STATE.md`（§3 能 / §4 不能 / §5 最可靠 / §6 最差）。

**更新时机**：能力增删、工具参数或语义变更、端点增删、门禁变化时。改这里时同步检查
`CURRENT_STATE.md` §3 的要点是否还成立。

**证据分级**：仓库内 = `path:line` 或命令，可复核；本地记录 = `dev/` 下的过程文档（不入库），
只作线索，不作依据；未验证 = 显式标注。

---

## 1. 能力地图

| 能力面 | 状态 | 入口 | 主要证据 |
|---|---|---|---|
| 多步 Agent 循环 | 已落地 | `agent_loop`、`avid --agent`、`POST /api/sessions/{id}/runs` | `src/avid/runtime/loop.py:102-295` |
| 工具调用协议（9 个工具） | 已落地 | 模型自主调用 | `src/avid/tools/__init__.py:45-92` |
| 工具参数校验与失败分类 | 已落地 | 循环内自动 | `src/avid/tools/validate.py`、`src/avid/runtime/execution.py:70-188` |
| 安全分层：三轴预设 + 四级 deny 阶梯 + bwrap 沙箱 + 审计 | 已落地 | `--permission {manual,auto,full}`、`--allow-full-access`、Web 选择器（full 二次确认）、`POST /runs {permission,full_access_ack}`、`~/.avid/policy.toml`、`~/.avid/audit/*.jsonl` | `src/avid/policy/{modes,action,rules,engine,sandbox,audit,permission}.py`、`src/avid/tools/shell.py` |
| 工作区（干活地点 / 权限边界 / 会话归属） | 已落地 | `--workspace`、`avid workspace`、导航列 ＋ / 🗑、`POST /api/workspaces`、`DELETE /api/workspaces/{id}` | `src/avid/workspaces.py`、`src/avid/svc/picker.py` |
| 会话持久化与分支 | 已落地 | `--session`/`--new-session`/`--list-sessions`/`--delete-session`、Web 分支选择器 | `src/avid/session/` |
| 上下文压缩（五步阶梯） | 已落地 | 自动（每轮 `context.prepare`） | `src/avid/policy/compaction.py`、`src/avid/runtime/context.py` |
| TODO 清单与提醒 | 已落地 | 模型调 `todo_write` | `src/avid/policy/todo.py`、`src/avid/runtime/state.py:179-187` |
| 待办清单面板 | 未重建 | 旧前端曾在输入条正上方常驻显示 `todo_write` 的清单，随阶段 32 删除；阶段 33 新前端尚未做这块，`todo_write` 工具本身未变（见 §2） | — |
| 子 agent 并行派发 | 已落地 | 模型调 `subagent`（≤4 个子任务） | `src/avid/tools/subagent.py` |
| 技能系统 | 已落地 | `skills/*/SKILL.md` + `load_skill`；`always: true` 的技能正文常驻系统提示；`GET /api/skills` | `src/avid/policy/skills.py` |
| 流式模型调用 | 已落地 | Web 运行路径（`?deltas=1` 订阅） | `src/avid/ai/client.py` 的 `stream_completion`、`src/avid/svc/runs.py` |
| 本地 Web 界面 | 已落地（阶段 33 重建） | `avid web --port 8765` 服务 API + SSE + 静态产物；产物由 `pnpm -C web run copy:dist` 交付，缺产物时非 `/api` 路径回 503 `static_missing` | `web/`、`src/avid/web/`；见 §11 |
| CLI | 已落地 | `avid`、`avid web`、`avid workspace` | `src/avid/cli.py` |
| 可观测（trace / 事件流 / 运行时状态） | 已落地 | stderr trace、`GET /api/runs/{id}/events`、`GET /api/runs/{id}` | `src/avid/runtime/events.py` |
| 评测与基准（AvidBench v0.1） | 已落地（第一层） | `python -m benchmarks.run`、`pytest -m eval` / `-m eval_smoke` | `benchmarks/README.md`、`benchmarks/avidbench/`、`BENCHMARK.md` §9 |
| 长期记忆、多 provider、多用户鉴权 | **不存在** | — | 见 `CURRENT_STATE.md` §4 |

---

## 2. 工具层（9 个）

注册表：`TOOLS`（`src/avid/tools/__init__.py:47-63`）与 `TOOL_IMPLS`（`:65-82`）名字一一对应，
由 `tests/test_tools_contract.py` 钉住。子 agent 用 `SUB_TOOLS` / `SUB_HANDLERS`（`:84-89`）——
**结构上去掉 `subagent` 自己**，因此不可能递归派发。

| 工具 | 参数（**加粗=必填**） | 边界与语义要点 | 实现 |
|---|---|---|---|
| `bash` | **command**；timeout_seconds | 在工作区根目录以 `bash -lc` 执行；默认 30s、上限 300s；输出上限 20000 字符，结果按「首尾节选 + 全文落盘」处理 | `src/avid/tools/shell.py` |
| `read_file` | **path**；offset；limit | 默认读 2000 行 / 20000 字符，截断时提示用 offset/limit 继续；`offset`、`limit` 最小值 1 | `src/avid/tools/files.py` |
| `write_file` | **path**、**content** | 整体写入，覆盖已存在文件，缺失父目录自动创建 | 同上 |
| `edit_file` | **path**、**old_string**、**new_string** | 精确替换一次；`old_string` 必须恰好出现一次，0 次或多次都不改并报错 | 同上 |
| `glob` | **pattern**；path | 按文件名匹配，`*` 不匹配点开头文件；结果上限 200 条 | 同上 |
| `todo_write` | **todos**（`[{content, status}]`，status ∈ pending/in_progress/completed） | **整份替换**当前 TODO 列表，空数组表示清空 | `src/avid/policy/todo.py` |
| `subagent` | **tasks**（`[{description, prompt}]`） | 并行执行互不依赖的子任务；**一次最多 4 个**；单子任务最多 30 轮、300s 超时；子 agent 看不到父对话，prompt 必须自包含 | `src/avid/tools/subagent.py` |
| `load_skill` | **name** | 读取技能全文；名字必须与系统提示里的技能目录一致 | `src/avid/tools/skill.py` |
| `web_search` | **query**；max_results | 检索公开网页，返回「标题 / 链接 / 摘要」清单（默认 5 条，上限 20）；**只在模型判断需要联网时调用**；Tavily 凭据来自 `TAVILY_API_KEY`（缺 Key → 回 `错误：` 并给出设置方法，不抛异常）；失败/空结果各给各的下一步；摘要按 800 字符截断 | `src/avid/tools/web_search.py`、`src/avid/tools/search_config.py` |

**执行协议**（`src/avid/runtime/execution.py`）：参数不是合法 JSON / 不是对象 / 工具未知 /
不符合 schema → **回文本、不抛异常、不触发事件、不计入 `tool_calls`**；实现抛异常 → 回
`工具执行失败：…`；业务拒绝原样透传。需要运行状态的 8 个工具以 `state=` 调用（`STATEFUL_TOOLS`，
`:55-66`，由契约测试与真实签名比对）；`web_search` 是**无状态**工具，HTTP client 由调用点
显式传入（正常运行不传、测试注入 `httpx.MockTransport`）。

---

## 3. 权限与工作区

### 3.1 三轴预设 + 四级 deny 阶梯（阶段 26）

判定顺序固定**先严后宽**，但「谁回答 REVIEW」与「沙箱在不在」是**两个正交的问题**
（`src/avid/policy/engine.py:decide`）：

| 步 | 判什么 | manual | auto | full |
|---|---|---|---|---|
| ① 硬拒绝 | 7 条 `DENY_PATTERNS` | ⛔ | ⛔ | ⛔ |
| ② 阶梯 deny | ADMIN / SYSTEM / PROJECT（凭据、`.git/hooks`、`.github/workflows`…） | ⛔ | ⛔ | ⛔ |
| ② 阶梯 ask | `.env` 一类「合法但敏感」 | 问人 | 分类器拒 | 放行 |
| ③ 越界 | 目标在工作区之外 | 问人 | 分类器拒 | 放行 |
| ④ 危险类别 | 15 类（提权、磁盘、服务、远程、容器…） | 问人 | 分类器拒 | 放行 |
| ⑤ 降级 | 要沙箱而后端不可用时的受管工具 | 问人 | 拒 | 不适用 |
| ⑥ 成本 | `subagent` | 问一次 | 放行 | 放行 |
| ⑦ 其余 | 区内只读 / 沙箱能保证的区内命令 | 放行 | 放行 | 放行 |

- **deny 高于 ask，ask 高于 allow**：下层 allow 永不抵消上层 deny，`full` 也不例外
  （`Ladder.check` 让 deny 全局优先；唯一放松点是 SYSTEM `[allow]`，且只对可放松的内置默认）。
- **沙箱是物理边界**（`src/avid/policy/sandbox.py`）：bwrap 只读挂载系统、可写挂载工作区、
  `--tmpfs` 掩蔽宿主凭据、`--unshare-net` 断网、`--clearenv` + 白名单环境；批准过的区外路径
  作为能力授予挂进来（且**掩蔽晚于授予**：批准父目录掀不开 `.ssh`）。
- **沙箱不可用不静默降级**：manual 逐个问人、auto 失败关闭，CLI/事件/界面三处可见
  （`SandboxSpec.degraded`）。
- **能力账本**：`("command", 原文)` / `("path", 绝对路径, ro|rw)` / `("tool", 名字)`，同一运行内
  命中即复用；subagent 与父运行共用一本（`src/avid/tools/subagent.py`）。
- **审计**：每条裁决（**含放行**）落 `~/.avid/audit/audit-YYYY-MM-DD.jsonl`，带三轴快照；
  写失败只计数不改裁决。
- **拒绝文案按 `kind` 分档**（`hard|credential|rule|danger|outside|degraded|cost`）：告诉模型
  「永远不许」还是「这次不行」，避免它换着花样重试。

三个预设：`manual`（默认，沙箱内免问、危险与越界问人）、`auto`（同样沙箱，分类器裁决、判不准
即拒）、`full`（不问、不套沙箱，**必须显式授权且不能作默认**）。决策表、阶梯清单、沙箱 argv
顺序与降级语义见 `docs/design/workspace-permission.md` §2–§6。

### 3.2 工作区

- **注册表**：`~/.avid/workspaces.json`（`AVID_HOME` 可覆盖），`REGISTRY_VERSION = 1`
  （`src/avid/workspaces.py:31-33, 52-58`）。
- **id 由根目录派生**（`w-` + sha1(绝对路径)[:12]）：所以重复登记同一个目录是幂等的，删掉
  注册表也不丢任何会话数据——**注册表是索引，不是权威**（`workspaces.py:1-13, 62-67`）。
- **会话归属**写在会话 header 的 `workspaceId` 字段；会话数据始终在各自工作区的
  `<root>/.avid/sessions/` 下（`src/avid/session/jsonl.py:94-108`）。
- **运行级工作区根**：`RunState.workspace_root` 决定文件类工具的相对路径基准、`bash` 的
  cwd 与压缩落盘的落点（`src/avid/runtime/state.py:49-51`、`tests/test_run_workspace.py`）。
- **界面新增工作区**：服务端弹宿主机文件夹选择器（`AVID_PICKER_CMD` → tkinter → zenity/kdialog
  → Windows/WSL → osascript 依次探测），取消什么都不做，重复返回 409 并切到已有项
  （`src/avid/svc/picker.py`、`src/avid/web/routes/workspaces.py:27-47`）。
- **注册表只由显式动作写**：启动与日常使用都不写盘（`workspaces.py:113-264`）。

---

## 4. 循环与扩展点

| 项 | 值 / 机制 | 证据 |
|---|---|---|
| 轮数 | **没有轮数上限**，因此也没有这个开关：循环靠「模型不再请求工具」收敛，中途可取消。轮数不是收敛判据——任何固定数字都会把「多读几个文件」判成失败 | `src/avid/runtime/loop.py`（`itertools.count(1)`，循环里没有上限判断） |
| 一步内的并发 | 同一轮的多个调用**按类别分段并发**：读类工具一起跑，写类/bash/任务类/子 agent 是串行屏障。上限 `AVID_MAX_PARALLEL_TOOL_CALLS`（默认 10，1 = 完全串行）；结果仍按源顺序回填。`subagent` 一次最多派 4 个子任务，整批共用一个 300 秒墙钟预算 | `src/avid/tools/safety.py`（分类）、`src/avid/runtime/execution.py`（`plan_segments` / `execute_batch`）、`src/avid/tools/subagent.py` |
| Stop 拦截 | `MAX_STOP_BLOCKS = 1`：Stop 回调可以要求「先别退出」，最多补 1 轮 | `loop.py:44, 250-272` |
| 取消 | 两个检查点（每轮开始前、每批工具执行前）；只在步骤边界抛 `RunCancelled`，不产生伪造的工具结果 | `loop.py:51-57, 186, 274` |
| TODO 提醒 | 连续 `TODO_REMINDER_AFTER_ROUNDS = 3` 轮未调 `todo_write` 就注入一条提醒（`NOTICE_ENTRY` 类型落库） | `src/avid/runtime/state.py:34, 179-187`、`loop.py:188-198` |
| 重复调用提醒 | 同名同参（键序无关）第 3、5 次各追加一次建议性提醒，只提醒不阻断 | `src/avid/runtime/hooks.py:334-384` |
| 工具输出上限 | `MAX_TOOL_OUTPUT_CHARS = 8000`，超限首尾节选，全文落盘 | `hooks.py:99, 287-331` |
| 注入点 | `UserPromptSubmit` 注入的上下文并进**系统提示词**（每轮重建、不落库），不改写用户消息 | `loop.py:67-99, 178-181` |
| 系统提示 | 默认文案（身份 / 工具契约 / 外部内容防线 / todo 约定，`policy/prompt.py`）+ 环境（工作目录、OS/架构/Python、当天日期、本次真正发出的工具清单）+ `<工作区根>/AGENTS.md` 引导块（缺失/为空即无块，超 16k 字符截断注明）+ always 技能正文 + 技能目录；SYSTEM 块首轮冻结保住 provider 前缀缓存 | `src/avid/policy/prompt.py`、`src/avid/runtime/context_manager.py` |

**Hook 四事件与默认注册**（`src/avid/runtime/hooks.py:89, 389-395`，注册顺序有意义）：

| 事件 | 默认回调 | 作用 |
|---|---|---|
| `UserPromptSubmit` | `context_inject_hook` | 注入工作区根与可用工具清单；可 BLOCK 整个输入 |
| `PreToolUse` | `permission_hook`、`log_hook` | 权限裁决（唯一调用 `gate` 的地方）；日志脱敏 |
| `PostToolUse` | `repeat_call_hook`、`large_output_hook`、`log_hook` | 重复提醒、截断与落盘、日志 |
| `Stop` | `summary_hook` | 收尾汇总 |

四种结果的表达：返回 `"block"` = 阻断；返回 `None` = 继续；**改** = 原地改 `context` 字典
（如 `content`、`denied_content`）；**注入** = 往 `context["injected"]` 追加。回调抛异常按
阻断处理（失败关闭），同一事件的所有回调都会执行，不短路（`hooks.py:144-154`）。

---

## 5. 上下文压缩（五步阶梯）

编排只在 `src/avid/runtime/context.py:69-168`，实现与阈值只在 `src/avid/policy/compaction.py`。

| 步 | 何时跑 | 做什么 | 花模型调用 |
|---|---|---|---|
| ① 工具结果预算 | 每轮 | 工具结果字符总量超预算时，把**最大的一项**落盘（最近 3 条永不落盘） | 否 |
| ② 条数裁剪 | 每轮 | 消息数超上限时裁中间、保留头 8 尾 24，切口只落安全边界 | 否 |
| ③ 免费瘦身 | 超**当前阈值** | 把较早的工具结果落盘，直到降到限值的 80% | 否 |
| ④ 摘要替换 | ③ 之后仍超限 | 先落盘完整记录，再用一次模型调用生成 `[历史摘要]` 替换历史 | 是（整个运行最多一次） |
| ⑤ 兜底 | 模型报上下文超限 | 总结更早历史、保留最近 5 条后重试一次 | 是（整个运行最多一次） |

阈值单一出处（`src/avid/policy/compaction.py:41-60`）：`TOOL_RESULT_CHAR_BUDGET = 200_000`、
`TOOL_RESULT_KEEP_RECENT = 3`、`MAX_MESSAGES = 50`、`SNIP_KEEP_HEAD = 8`、`SNIP_KEEP_TAIL = 24`、
`CONTEXT_CHAR_LIMIT = 400_000`、`MICRO_COMPACT_KEEP_RECENT = 3`、`MICRO_COMPACT_TARGET_RATIO = 0.8`、
`REACTIVE_KEEP_RECENT = 5`、`SUMMARY_MAX_TOKENS = 4000`、`WINDOW_TRIGGER_RATIO = 0.8`、
`MIN_CHARS_PER_TOKEN = 0.5`、`MAX_CHARS_PER_TOKEN = 6.0`。

**③④ 的阈值随真实窗口派生**（`context.effective_budget` → `compact.derived_context_chars`）：
有窗口、且拿到过一轮真实读数时按``窗口 × 0.8 × 实测 chars/token −（系统提示 + 工具定义 字符）``
现算；`chars/token` 由 `RunState.last_usage.prompt_tokens` 与 `RunState.prompt_parts`（发出那次
请求前记下的三块字符数）这一对反推——所以中英混排的语言差异自动被吸收，而不是靠一个查表来的
语言系数。缺任一项（没窗口、没读数）就**逐字**回落到 `CONTEXT_CHAR_LIMIT`；调用方显式注入
阈值时用 `ContextBudget(from_window=False)` 关掉派生（否则注入值会被盖掉）。派生理由会拼进
`context_compacted` 事件的 `detail`，界面据此回答"为什么现在压"。E2E：`benchmarks/context_window/`
（真循环 + 按窗口记账的标尺模型，4 个臂给 5 条布尔结论）。

**落盘（spill）**：目录 `.avid/context`（相对运行级工作区根），文件名
`<kind>-<run_tag 或进程标识>-<seq:04d>.txt`，回给模型一句 `[已落盘] …用 read_file 读回`；
`run_tag` 是每次运行的短标识，防止两次运行互相覆盖（`compaction.py:54-131`、`state.py:86`）。
落盘失败记日志并跳过本次压缩，不抛异常；摘要失败保留原历史（`compaction.py:113-127, 155-174`）。

压缩发生时发一条 `context_compacted` 事件（带 step/detail/before/after）并累加
`state.compactions`（`context.py:48-62`）。**"压完还剩多少"由压缩之后下一轮的真实
`prompt_tokens` 回答**（阶段 22，`state.mark_compacted` → `record_usage`），不在压缩层估算。

---

## 6. 会话与分支

公开门面 45 个名字（`src/avid/session/__init__.py:75-130`，由 `tests/test_session_facade.py` 钉住；
**改它就是公开接口变更**）。

| 能力 | 说明 | 证据 |
|---|---|---|
| 新建 / 续接 / 列举 / 删除 | CLI 四个会话 flag；Web 有对应端点 | `src/avid/cli.py:120-137`、`src/avid/web/routes/sessions.py` |
| 条目树 | `Entry(id, parent_id, seq, timestamp, type, message)`；`parent_id` 串链，**提交后不可变** | `src/avid/session/types.py:40-53` |
| 条目类型 | `message`（用户/助手/工具结果）与 `notice`（内核注入的提醒）——界面靠类型分辨，不靠文本前缀 | `types.py:27-31` |
| 值 | 地址式命名空间：`avid.session.name` / `avid.entry.label` / `avid.branch.tip` | `src/avid/session/values.py:19-21` |
| 分支 | **分支只是「链尾是谁」的一个值**；`main` 是隐式默认分支，读侧永远把它排第一 | `values.py:23-26, 54-55`、`session/branch_names()` |
| 分叉 | 可从任一历史条目开新分支；活动 run 期间 409、重名 409 | `src/avid/web/routes/sessions.py:61-70` |
| 两后端 | 内存与 JSONL 共用一套一致性用例（参数化到两个后端） | `tests/test_session_conformance.py`、`tests/session_cases.py` |
| JSONL 落盘 | 首行 header + 每次提交一行 JSON；`flush + fsync` 后才算提交；文件名 `时间戳_<id>.jsonl` | `src/avid/session/jsonl.py:1-24, 618-643, 684-688` |
| 跨进程锁 | 旁挂 `<会话文件>.lock`，读之前就取锁，不是锁会话文件本身 | `jsonl.py:498-535, 566-579` |
| 残片自愈 | 末行非法 → 当残片丢弃并原子重写；中间行非法 → 报错带行号（不静默丢数据） | `jsonl.py:585-611` |
| 短写回滚 | 一次 `os.write` + 长度校验，短写截回原大小再抛错 | `jsonl.py:414-452` |
| 投影 | `messages_for_branch` / `entries_to_messages` / `repair_incomplete_batches` 是唯一的「读出来」路径 | `session/__init__.py:8-9` |
| 唯一写入者 | `SessionRecorder`；A11 门禁断言 `web/`、`svc/` 不直接写 | `tests/test_web_boundaries.py:151-153` |
| 变更线 | `MutationLine`：单写者，同线程重入抛 `SessionBusyError`，关闭时先 seal 再等空闲 | `src/avid/session/mutation.py:23-70` |

---

## 7. 任务图（跨会话 Task DAG）——已下线（阶段 27）

六个任务工具（`create_task` / `update_task` / `can_start` / `claim_task` / `complete_task` /
`get_task`）与它们的存储（`<工作区根>/.tasks/{id}.json`、`TaskStore`）、应用服务
（`svc/tasks.py`）、只读接口（`GET /api/tasks{,/{id}}`）与前端页面（`/tasks`）在阶段 27
一并删除；工具数 15 → 9。

删除理由与当时的实现记录见 `docs/design/runtime-architecture.md` §17（该章开头有下线横幅）。
一句话：它的**唯一消费者**是那个只读页面，而"这次对话拆成了哪几步、走到第几步"由 `todo_write`
承接。旧前端曾把它推导成输入条上方的待办清单；阶段 33 的新前端尚未重建这块面板（见 §11）。
旧的 `<工作区根>/.tasks/` 数据不迁移、不删除，只是不再被读。

---

## 8. 技能

| 技能 | 一句话描述 | 证据 |
|---|---|---|
| `agent-builder` | 从零搭一个 agent 运行时——分层顺序、每层的验收标准与最常见的失焦点 | `skills/agent-builder/SKILL.md` |
| `code-review` | 审查一次改动——先对规格再看实现，按严重度排序给出可执行的意见 | `skills/code-review/SKILL.md` |
| `pdf` | 读取与提取 PDF 内容——先判断有没有文本层，再决定用解析还是 OCR | `skills/pdf/SKILL.md` |

机制：扫描 `<工作区根>/skills/*/SKILL.md`；frontmatter 只认单行 `key: value`（不引 YAML 依赖），
`name` 缺省取目录名、`description` 缺省取正文首个非空行；frontmatter 另认 `always: true`（true/yes/1/on），标记的技能正文（剥 frontmatter）常驻进系统提示，上限在 `policy/prompt.py`：单篇 8k 字符截断注明、总量 16k 超限按名字序跳过；**解析发生在每次运行**（`RunState.for_run`
里重新扫描），不在 import 时（`src/avid/policy/skills.py`、`src/avid/runtime/state.py:112-123`）。

分发：系统提示里只放**非 always** 技能的 `- name: description` 目录（always 的正文已常驻，不再列目录，避免诱导一次多余的 `load_skill`）；其余正文由模型调 `load_skill` 按需读取，按注册表
key 查而不当作文件路径，未命中返回可用清单（`skills.py:108-140`）。Web 的 `GET /api/skills` 与
系统提示同源，服务端缓存 5 秒（`src/avid/svc/__init__.py:80`）。

---

## 9. 模型接入

- **协议**：OpenAI 兼容 `POST {base_url}/chat/completions`，httpx 直连、不套 SDK（请求体与响应
  字段保持可见）；base_url 默认 `https://api.openai.com/v1`，模型名与 key 走环境变量
  （`src/avid/ai/config.py`、`src/avid/ai/client.py`）。
- **两条路径同形**：非流式 `chat_completion` 与流式 `stream_completion` 都返回同一个 `Turn`
  （文本 / 工具调用 / usage / finish_reason）；流式按 SSE 解析并把分片累加（`ai/client.py`）。
  生产 Web 路径用流式跑主轮次、用非流式跑摘要，两者分开注入，delta 流里因此不会混进摘要文本
  （`src/avid/runtime/loop.py:110-113`、`src/avid/svc/runs.py`）。
- **超时**：连接 10s、读 60s（`ai/client.py:22-35`）；进程级复用 HTTP 客户端。
- **用量与缓存台账（阶段 22）**：`ai/usage.py` 是唯一的 provider adapter，把四家写法归一成
  同一个 `Usage`（`prompt_tokens` / `completion_tokens` / `total_tokens` 三基数 +
  `cache_read_tokens` / `cache_write_tokens`，后两者可空）；OpenAI 的
  `prompt_tokens_details.cached_tokens` 与 `prompt_cache_hit_tokens`（DeepSeek 系）、Anthropic 的
  `cache_read_input_tokens` / `cache_creation_input_tokens`、Gemini 的 `usageMetadata.cachedContentTokenCount`
  都认。流式与非流式两条解析路径共用它。累计量仍逐轮加进 `state.tokens`；
  `RunState.usage_report()` 另有上下文占用（最近一轮 `prompt_tokens` / 窗口 / 占用率 /
  **三块字符占比分配**：系统提示词与工具定义从不发给前端，只有内核在发请求前算得到）、
  缓存（读写 / 命中率）与压缩（次数 / 压缩后读数）三块，进 `run_status` 与 `run_finished`，
  **按分支落盘**进会话值（窗口按 `AVID_CONTEXT_WINDOW` → 内置模型名小表 → **provider 的
  `/models`**（`context_length`，进程内缓存、失败静默、`AVID_MODEL_INFO=off` 关掉）取值；
  三条都没有就只报 tokens、不算占用率）。
  **没有成本估算**（不做价格表；`runtime-architecture.md` §20）。
- **错误**：4xx/5xx 一律 `LLMError` 上抛终止运行；上下文超限是唯一会重试的错误（兜底压缩 +
  重试一次）。
- **联网检索的凭据是独立的**：`web_search` 用 `TAVILY_API_KEY`（可选，另有 `TAVILY_BASE_URL`），
  由 `src/avid/tools/search_config.py` 在**调用时**读环境变量。它不进 `Config`、不参与
  `load_config()` 的必填校验——没配它只让 `web_search` 失败关闭，模型调用、会话、Web 服务不受影响。

---

## 10. Web API

`API_VERSION = 1`；全部挂在 `/api` 前缀下，未知 `/api/*` 回 JSON 404、绝不回落 SPA
（`src/avid/web/app.py:122-125, 210-221`）。共 **22 个端点**：

| 方法 + 路径 | 职责 |
|---|---|
| `GET /api/health` | 健康探针 |
| `GET /api/meta` | 版本 / features / event_types / capabilities / stream / build |
| `GET /api/skills` | 技能目录（与 system prompt 同源） |
| `GET /api/workspaces` | 候选工作区 |
| `POST /api/workspaces` | 登记工作区（已存在 → 409 `workspace_exists`） |
| `DELETE /api/workspaces/{id}` | 从候选里移除工作区（只摘索引；会话归「未归属」，绑定值 → 409 `workspace_bound`） |
| `POST /api/workspaces/pick` | 弹宿主机文件夹选择器（取消返回 null，无后端 503） |
| `GET /api/sessions` | 会话列表 |
| `POST /api/sessions` | 建会话（`workspace` 必填，缺了 400） |
| `GET /api/sessions/{id}` | 会话详情 |
| `PATCH /api/sessions/{id}` | 改名 |
| `DELETE /api/sessions/{id}` | 删除 |
| `GET /api/sessions/{id}/entries` | 条目分页（`branch` 默认 main；默认 `limit=100`、硬上限 500、游标排他） |
| `GET /api/sessions/{id}/branches` | 分支列表（每项带该分支最近一次运行的 `usage` 快照，阶段 22） |
| `POST /api/sessions/{id}/branches` | 在某条目处分叉 |
| `POST /api/sessions/{id}/runs` | 起一次运行（201，带 `branch` / `permission`） |
| `GET /api/runs/{id}` | 运行状态（权威终止以此 + 已提交条目为准；带 `usage` 快照） |
| `POST /api/runs/{id}/cancel` | 请求取消（202，下一检查点生效） |
| `GET /api/runs/{id}/approvals` | 当前待决审批 |
| `POST /api/runs/{id}/approvals/{aid}` | 答复审批（幂等；换结论 409、过期 410、未知 404） |
| `GET /api/runs/{id}/events` | SSE 事件流（`?after=` > `Last-Event-ID` > 0；`?deltas=1` 订阅增量） |

**事件分三档**（`src/avid/runtime/events.py:52-73`，共 19 类）：

| 档 | 数量 | 事件 | 语义 |
|---|---|---|---|
| durable | 16 | `run_started`、`user_message`、`assistant_message`、`tool_result_message`、`tool_call_started`、`tool_call_finished`、`tool_call_denied`、`approval_requested`、`approval_resolved`、`context_compacted`、`todo_reminder`、`stop_nudge`、`run_finished`、`run_failed`、`run_cancelled`、`resync` | 带 `id`/`seq`，可重放；重连从最后一个 durable 点续 |
| transient | 1 | `run_status` | 不带 `id`，断了就断了 |
| delta | 1 | `assistant_delta` | 不带 `id`，可任意丢；默认不投递，需 `?deltas=1`；不落盘、不重放、不占 durable 重放预算 |

**features 开关**（`src/avid/svc/__init__.py`，全为 `1`）：`approvals`、`cancel`、
`sessions`、`entries`、`deltas`、`branches`、`workspaces`、`permission_modes`、`security_layers`、
`full_access`、`workspace_picker`、`workspace_delete`、`usage`。
客户端读 features 决定启用哪些能力，只在加特性时升 `api_version`。

**并发与保留**：同时最多 24 条 SSE 流（超出 503 `too_many_streams`）；每 run 重放缓冲 512 条
durable、事件总数上限 4096、终态记录保留 600s 或最多 200 个 run；心跳 15s、静默兜底 30s；
审批超时 120s（`svc/__init__.py:64-110`、`svc/runs.py:52-69`、`svc/approvals.py:31`、
`runtime/events.py:116-118`）。

**信任边界**（`src/avid/web/app.py:49-120`）：默认只监听回环；`TrustBoundaryMiddleware` 校验
`Host` 白名单（挡 DNS rebinding）与 `Origin`（挡 CSRF），可用 `AVID_ALLOWED_HOSTS` 追加；
响应带 CSP / `Referrer-Policy` / `X-Content-Type-Options`。**没有认证**——不是鉴权，只是
「别让浏览器替别人发请求」。细则见 `docs/guide/web-ui.md` §2.1。

---

## 11. 前端

**已随阶段 33 在分支 `refactor/web-hana-ui` 从零重建（2026-10）。** 技术栈 React 18 +
TypeScript + Vite + pnpm 10；视觉是**纸本语言**（暖纸 / 青夜两主题，token 层
`web/src/styles/tokens.css`；EB Garamond / PT Serif / Inter / JetBrains Mono 四套自托管
字体，子集裁到 latin + latin-ext，CJK 走系统回退）。渲染层自研：`markdown/`（块级 +
行内 + 渲染，全程 React 元素、无 `dangerouslySetInnerHTML`、SVG 走 `data:` `<img>`）
与语法高亮 tokenizer（九种语言，token 拼回逐字等于原文）。

**页面**（`web/src/surfaces/conversation/`）：时间线（思考块 / 工具卡 / 消息动作行的复制
与分支 / 用量卡）、输入区（发送/停止、权限胶囊、按运行的模型选择）、会话管理（新建 /
行内重命名 / 确认删除）、工作区文件夹式分组与删除、设置面板、组件墙（`?gallery=1`）。
状态三件：`api/client.ts`（网络出口唯一层）、`api/events.ts`（SSE 消费，`after` 游标 +
deltas）、`state/useRunStream.ts`（事件收敛）。

**门禁（收口恢复）**：`pnpm -C web run verify` = typecheck + vitest（204 用例）+ build +
`gate:size`（体积预算 `web/budget.json`：首屏 JS gzip 70,192 B / 上限 88 KiB、样式表
6,806 B / 16 KiB、字体 24 文件 815 kB / 1 MiB、位图纹理 0）；两侧对账接回——
`tests/test_wire_contract.py`（27 对 DTO 字段名双向相等）、`tests/test_event_contract.py`
（事件集合相等）、`test_modes.py` 词表第四处、A12 第三方直连门禁（阶段 9 已恢复）。
产物交付 `pnpm -C web run copy:dist` → `src/avid/web/static/`。

**尚未重建**：浏览器 e2e（旧 Playwright 套件已删，界面验收走实机 CDP 脚本
`dev/evidence/`）、a11y 与视觉回归基线、分层 / token 白名单 / 对比度门禁（旧脚本按
玻璃视觉的规则集写，纸本 token 层需要自己的一套）。

---

## 12. CLI

| 入口 | 用法 | 说明 |
|---|---|---|
| 提问 | `avid "问题"` | 单轮问答；stdout 只放回复，stderr 放 trace 与 token 用量 |
| Agent | `avid --agent "…"` | 走循环，模型可调用 9 个工具；help 里的工具清单从 `TOOLS` 派生 |
| 审批 | `--yes` | 替所有审批答「是」（**硬拒绝与阶梯 deny 仍然生效，沙箱也不关**） |
| 权限 | `--permission {manual,auto,full}` + `--allow-full-access` | 缺省按工作区默认权限，没设过就是 `manual`；`full` 必须同时给第二个开关，否则拒绝启动 |
| 工作区 | `--workspace PATH\|ID` | 缺省当前目录（会打印解析结果） |
| 会话 | `--session ID` / `--new-session` / `--session-name NAME` / `--list-sessions` / `--delete-session ID` | 前两个隐含 `--agent`、互斥；`--session-name` 需配二者之一 |
| Web | `avid web [--host] [--port] [--workspace] [--reload]` | 默认 `127.0.0.1:8765`；非回环监听打印警告 |
| 工作区管理 | `avid workspace add <path> [--name] [--permission]` / `list` / `remove <id\|path>` / `permission <id\|path> <mode>` | 登记、列举、摘索引、设默认权限 |

会话落盘在 `<工作区>/.avid/sessions/`；CLI 是**唯一**同时认识 `runtime` 与 `session` 的接线处
（`src/avid/cli.py:1-16`）。

---

## 13. 评测与基准（AvidBench）

仪器在 `benchmarks/`，**不进 wheel**——它是本地评测用的，不随包分发。

- **任务集**：两套 suite 共 21 条只读 case。`cases/v0/`（12 条：基础 4 / 长程 3 / 恢复 2 /
  子 agent 1 / 任务 1 / 会话 1）与 `cases/v1/`（9 条难度 case，`tier` 3–5，分长上下文 /
  依赖规划 / 委派校验三组）。fixture 在 `benchmarks/fixtures/`（225 个文件），真跑时复制进
  临时目录、跑完即删，`fixtures/` 永不被写。
- **版本规则**：一个目录 = 一个冻结版本；有基线落盘后不再改，要改就新建 `cases/v2`。
  `--suite v0|v1|all` 是唯一选择器；**跨 suite 的差值不可比**。
- **变体**：`bare`（自写朴素循环 + `read_file`/`glob`/`bash`）/ `core`（真 `agent_loop`，工具集与
  bare 完全相同）/ `full`（全工具与全机制）。三者共用同一份系统提示词，规格写进每次运行的
  `result.json.variant_spec`。
- **判定器**：`command` / `file_contains` / `file_equals` / `json_path_equals` / `answer_contains`
  五种确定性原语；`resolved = 全部通过`（AND，无加权、无部分分）；**没有 LLM judge**。
  fixture 不变量类判定器在干净 fixture 上必须先通过。
- **单变量对照**：`--context-chars N` 经 `agent_loop(budget=...)` 注入压缩阈值（内核唯一开口，
  默认 `None` 行为不变）；注入值写进 `result.json.overrides`，`bare` 不记（它没有压缩）。
- **入口**：`python -m benchmarks.run [--suite|--cases|--variants|--context-chars|--smoke|--list]`；
  pytest 薄壳 `pytest -m eval` / `-m eval_smoke`（默认不跑、不进 CI）。
- **落盘**：`benchmarks/runs/<UTC 时间>-<commit>/<case>/<arm>/{result.json,trajectory.jsonl,answer.txt}`
  加 `summary.txt` 与 `results.json`；`runs/` 在 `.gitignore` 里。
- **基线**：v0 见 `BENCHMARK.md` §9，v1 与三档阈值对照见 §10——两个 suite 上三臂都全过
  （只读任务被 `bash` 折叠掉），对照量到的是抖动（core ±3.7% / full ±8.5%），不是机制。
- **它测的是内核循环**：runner 直连 `agent_loop`，不经 `svc.RunRegistry`，所以数字不代表 Web
  服务层的运行语义。
- **边界门禁**：A14——产品代码不许 import `benchmarks`；仪器留在 `src/` 之外、`runs/` 不入库。

---

## 14. 分发、门禁与 CI

- **依赖**：内核只需 `httpx`；`web/` 是唯一 importer of FastAPI/uvicorn/pydantic，在
  `[project.optional-dependencies].web`（`pyproject.toml:11-14`），由 A1/A2 门禁守住。
- **交付**：`uv build` 出 wheel，前端产物作为静态资源随 wheel 分发；安装者不需要 Node。
- **静态门禁**：`ruff`（规则集显式钉住，不跟默认值漂）、`mypy`（`files = ["src/avid"]`）。
- **CI 三个 job**（`.github/workflows/ci.yml`）：内核（ruff + mypy + `pytest -q`）、
  stress（`pytest -q -m stress`）、前端（build + `gate:size` + typecheck + test——收口恢复的
  体积门禁已进 CI；分层 / token / 对比度与 e2e 尚未重建，见 §11）。

---

## 15. 明确没有的能力

长期记忆、沙箱执行、多 provider、多用户与鉴权、中断后恢复运行、SQLite 后端、
**成本估算**（token 用量台账本身已落地，阶段 22——占用、缓存读写与命中率、压缩次数，
按分支落盘）、跨进程的「一个会话一个活动 run」互斥、subagent 子事件转发、前端虚拟列表
（新前端暂无虚拟列表，条目量大时立项）
——逐条证据与分类见 `CURRENT_STATE.md` §4，数字现状见 `BENCHMARK.md`。
