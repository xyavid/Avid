# 内核：数据流与生命周期

CLI 与 Web 是**两个平级接线点**，内核不知道有几个调用方（`services/` 是第二个调用方）。一次运行按下面这个顺序发生。

```text
CLI  avid --agent / --session ─┐
Web  POST /api/sessions/{id}/runs ─┴─► services/runs.RunRegistry（线程 + 事件重放缓冲）
                                       ▼
                         services/assembly.assemble() —— 建 state + 起 MCP + 组工具表 + resolve spec
                           （唯一构造 Run 的地方；注入工具表时不起 MCP，传了 recorder 才接计划与压缩游标）
                                       ▼
                         agent/run.Run —— 只表达调度顺序（subagent 递归用）
                           ├ ContextManager.compose()  装配 SYSTEM / tail 块，编排压缩
                           ├ ContextManager.call() → Turn   正文 + tool_calls；报超限则压缩后重发一次
                           └ execution.execute_batch()
                                ├ security/action 归一化 → security/engine 裁决（默认放行；毁灭级问一次）
                                ├ security/sandbox 按能力账本组装 bwrap argv
                                └ 工具 handler（agent/tools/*，含 MCP 包装）
       on_message ─► SessionRecorder ─► <会话目录>/<工作区 id>/*.jsonl（durable 真相；默认 ~/.avid/sessions）
       on_event   ─► RunRegistry 缓冲 ─► SSE ─► 浏览器消费方（React 前端 web/）
       ── 待决：一张表两种 kind —— 毁灭级裁决（allow/deny）与模型的提问
          （ask_user 的 question/options → answer）；同一组端点、同一个界面槽，超时对提问
          是「没答」（模型据此降级），对审批是「拒绝」
       ── 补充输入：POST /api/sessions/{id}/inputs（mode=now|after）
          → services/inbox.SessionInbox（会话级内存表）→ run 线程在轮次边界领取
          → transcript.append + on_message 落库（与首条消息同一条路，写者仍只有 recorder）；
          队列跨刷新、不跨进程；没赶上的 now 降级为 after（不消失）
       ── 旁路（可丢的派生层）：装配层在提交成功后 notify(session_id)
          ► avid/index 的队列（同会话合并） ► 增量扫描 JSONL ► ~/.avid/index/sessions.sqlite
          ► GET /api/search 与 `avid session search` 读它；列表仍以 JSONL 为准
```

## 不变量（破一条就是 bug）

- **`on_message` 是消息的唯一出口**：循环不 import 会话层，落库与否由回调决定。
- **`on_event` 是步骤级事实的通道**，不是第二个消息通道；事件不写进会话 JSONL。
- **索引是可丢的派生层**：JSONL 是唯一权威，库里的每一列都能从 JSONL + 注册表重算；写失败只落后（`last_error`），`avid index check|rebuild` 是恢复路径。通知只从装配层发，`session/` 不知道索引存在（A14）。
- **会话存储**集中在一个专用目录：解析单点是 `security/userdirs.sessions_dir()`（`AVID_SESSIONS_DIR` → `~/.avid/settings.json` 的 `sessions_dir` → `~/.avid/sessions`），目录内按工作区 id 分子目录；归属只认 header 的 `workspaceId`。旧位置用 `avid session migrate` 显式搬（先出清单再动手）。

## 运行输入与状态

- `agent/spec.py` 收口一轮由什么组成（模型配置、chat、工具表与 schema、各项上限）；`agent/state.py` 的 `RunState` 是权限、沙箱、工作区、钩子与全部可变状态的唯一权威。一份事实只有一个所有者：spec 不重复持有任何 state 字段。
- 宿主把这两样建成一体的地方是 `services/assembly.py`：`assemble()` 是「起一个 run」的单点，五步顺序固定，产出的 `RunAssembly.run()` 是全仓唯一构造 `Run` 的地方——所以没有哪条宿主路能悄悄换掉工具表或漏掉计划槽。
- 每步产出一个 `Turn`（正文 + tool_calls）；`agent/stop.py` 的 `StopReason` 是唯一终止路径，连续拒绝停机（`denial_halt`）也在那里产出。
- **跨 run 的意图存会话值，不在 RunState 里**：usage、压缩游标与任务计划（`avid.plan/<分支>`）都是值，宿主起 run 时读进 RunState、用回写回调交回 recorder；计划全部 completed（或清空）时删值，`/rewind` 跟着删。判断一份状态该存哪：Transcript 拥有说过/做过，会话值拥有跨 run 的意图，RunState 拥有本轮派生量。

## 上下文与压缩

- 装配在 `agent/context.py`：`CONTEXT_MAP` 声明表一行一个块（kind / 标题 / 段落 / 稳定性 / 上限 / 取值），顺序即渲染顺序即缓存纪律——五个稳定块在前，每轮块（注入文本 / 计划 / 运行状态）在后，拼成末端那条不进 transcript 的注记；计划块的**源数据**是会话值，注记只是它这一轮的视图。
- 压缩通路在 `agent/compaction.py`：保留最近 N 轮 + 一段结构化检查点（已验证事实与假设分开放）；阈值触发、`/compact`（`ContextManager.compact_now`）与溢出兜底（`ContextManager.call` 捕获 `PromptTooLongError` 后压缩重发一次）共用一个入口，预算字段集中在 `ContextBudget`。压缩那次调用由 `ContextManager._run_compaction` 交出冻结的 system 与工具表，请求于是与上一轮同前缀（求的是一次缓存命中），请求里唯一的增量是末尾那条指令；模型若发起工具调用、答空或报错，回退到 `COMPACTION_SYSTEM` + 拍平文本的独立引擎提示词路径，两条都不成才保留原历史。
- 循环的四个点各有两段：本体那段在 `agent/guards.py`（Pre 的权限闸门、Post 的输出预算与重复提醒、Stop 的运行汇总，普通函数，执行路径直接调用），额外配置那段是 `agent/hooks.py` 按工作区 `.avid/hooks.json` 装配的用户脚本（默认无声明＝无钩子）；两段都不改工具调用与结果的形状，脚本只能判定与注入。
- **本文件自身也是上下文**：workspace 根目录的 `AGENTS.md` 进 `BOOTSTRAP` 块（标题「工作区约定」），字符上限是 `prompt.AGENTS_MD_MAX_CHARS`（16000）——这是它必须保持精简的原因，体积由 `tests/test_docs_contract.py` 守住（预算 6000）。

## 跨包分层门禁

`tests/test_web_boundaries.py` 用 grep 与 AST 断言钉住跨包依赖方向，不跑任何东西（A1–A15）：

- **A13**：循环与工具协议对策略层零运行时依赖——调度不 import `security/`，裁决只经 `agent/execution.py` 的边界。
- **A14**：会话索引对会话层单向只读——`session/` 不得 import `index/`；`index/` 不得依赖 `services/`、`web/`、`agent/`、`providers/`。
- 内核包（`agent` / `providers` / `security` / `session` / `services`）在模块层只许 `httpx` 一个第三方依赖，由 `tests/test_runtime_deps.py` 拦；扩权要在 `pyproject.toml`、`AGENTS.md` 与该门禁里显式承认。

## 运行期依赖

内核 Python 3.12，运行期依赖只有 `httpx`；web 栈（fastapi / uvicorn / pydantic）在 `[web]` extra 里，CLI-only 安装不会拉进来。
