# 改动落点

先查 `AGENTS.md` §6 那张十行表，这里收其余与时序细节。原则：**能由已有表派生的表不许另立一份**，每个单点声明都指向代码里唯一的那个位置。

## 工具与执行

| 要改什么 | 动哪里 |
|---|---|
| 新增工具 | 在实现函数上挂 `@tool(...)`——`agent/tools/registry.py` 是单点（schema、并发档、writes 全在这一处），其余表全部派生 |
| 改并发档 / 判据 | `@tool(concurrency=…)` 三档：`safe`（只读，可并行）/ `exclusive`（屏障）/ `conditional`（配 `assess(arguments, state)`，**只能把默认独占降成并行**）；bash 的只读判定在 `security/command_parse.is_read_only`，判据表与用例同在 `tests/test_tool_concurrency.py` |
| 改 MCP 工具怎么包装 | `agent/tools/mcp.py` |

## 循环、上下文、协议

| 要改什么 | 动哪里 |
|---|---|
| 新增会话内命令 | `agent/commands.py` 的 `COMMANDS` 加名字 + 执行分支（CLI 与 Web 自动继承解析） |
| 新增模型协议 | `providers/` 加一个实现模块 + `__init__.py` 的 `PROVIDERS` 一条表项，对循环返回**同形** `Turn` |
| 新增一类上下文 | `agent/context.py` 的 `ContextManager.register_source(kind, fn)` 一行 |
| 改压缩阈值 / 检查点结构 | `agent/compaction.py`（保留最近 N 轮 + 摘要；阈值触发与溢出兜底共用一个入口） |
| 改系统提示词 | `agent/prompt.py` |
| 改本体在四个事件上的行为（权限闸门 / 输出预算 / 重复提醒 / 运行汇总） | `agent/guards.py`：普通函数，`execution.py`（Pre/PostToolUse）与 `stop.py`（Stop）直接调用；闸门是唯一持有策略层依赖的 agent 模块（`tests/test_web_boundaries.py` A13） |
| 加/改用户钩子脚本 | 声明在用户级 `hooks.json`（位置单点 `security/userdirs.hooks_config_path()`，一份管全部工作区，且在工作区外——运行写不到自己的闸门），`{"hooks": [{"event", "command", "args", "env", "timeout_seconds"}]}`，解析与执行都在 `agent/hooks.py`（路径由 `agent/state.py` 传入）；脚本 stdin 收事件 JSON、stdout 回 `{"decision", "reason", "injected"}`，坏输出 / 超时 / 非零退出一律按拦截，事件字段按 `EVENT_FIELDS` 白名单挑（内核对象不进子进程）；新增事件要同时改 `EVENTS` 与该白名单 |

## 会话与索引

| 要改什么 | 动哪里 |
|---|---|
| 改会话存哪 / 目录布局 | 解析单点 `security/userdirs.sessions_dir()`（环境变量 → 设置文件 → 默认）；布局在 `services/workspace_registry.sessions_root()`（`<会话目录>/<工作区 id>`）；界面在 `web/src/components/settings/SessionsSection.tsx` |
| 改索引哪些文本 / 怎么检索 | `avid/index/extract.py` 是「哪段文本进 search_text」的唯一规则处；查询语法与作用域在 `avid/index/queries.py`（用户输入当字面量，少于 3 字回落 LIKE）；schema 变更走 `avid/index/db.py` 的 `MIGRATIONS` 追加一条 |
| 改会话标题 / 自动命名 | 规则在 `services/runs.py`：`session_title(content)`（首条消息取一行、24 字截断、纯图给「（图片）」）与 `_name_from_first_message`（只在**本会话还没有名字**且这是历史里第一条用户消息时写，写者仍是 run 自己的 recorder）；名字是会话值（`avid.session.name`），改名入口在 `services/sessions.rename` |
| 改一个 run 怎么装配（权限槽 / 工具表 / 计划与检查点接线 / 回调） | `services/assembly.py` 的 `assemble()` 是单点——它按固定顺序建 `RunState`、起 MCP、组工具表、`RunSpec.resolve`，`RunAssembly.run()` 是全仓唯一构造 `Run` 的地方（`Run(` 只许再出现在 `agent/tools/subagent.py`，A3 门禁钉住）；`tools=` 一旦注入就不起 MCP（测试与 benchmark 的口径），传了 `recorder` 才接计划与压缩游标。session / recorder / repo / indexer 的开关仍归各宿主 |
| 改补充输入 / 排队语义 | `avid/services/inbox.py`（表与三条不变量）+ `agent/run.py` 的唯一交付点；端点面在 `web/routes/inputs.py`，界面在 `Composer.tsx`（忙时两动作）与 `Timeline.tsx`（待办标记） |
| 旧布局搬迁 | `services/session_migration.py`，只由 `avid session migrate` 触发 |

## 模型与接口

| 要改什么 | 动哪里 |
|---|---|
| 改思考（CoT）怎么存 / 怎么回传 | 判定单点在 `providers/reasoning.py`（记录形状 + 「同路由 + 工具链才回传」）；各族的线格式在 `providers/{openai_compat,anthropic,responses}.py`（openai 补 `reasoning_content`、anthropic 回传带签名的 thinking 块、responses 只归档）；对外发送是白名单，记录字段不许漏上线路 |
| 改 BYOK 配置界面 / 拉取模型清单 | 界面在 `web/src/components/settings/ModelSection.tsx`（表单 / 连通校验 /「获取模型」）；端点面在 `avid/web/routes/settings.py`（`/settings/byok` 的 GET·PUT·DELETE、`/test`、`/models`）；配置校验与落盘单点在 `providers/byok.py`，列模型的请求形状在各实现的 `list_models`（每一族自己的 URL 与鉴权），共用解析在 `protocol.model_ids` |
| 改联网（检索后端 / 取文规则 / 地址护栏 / 上限） | 模型可见的名字与参数单点在 `agent/tools/web.py`（换检索后端 = 换这一层之下的实现，模型侧不动）；取文规则在 `agent/tools/extract.py`（零依赖的链接密度启发式，已知边界写在模块注释）；配置从哪来、谁说了算单点在 `avid/web_settings.py`（env > settings.json 的 `web` 块 > 默认，与 sessions_dir 同形；工具与设置端点读同一份解析），界面在 `web/src/components/settings/WebSection.tsx`，端点在 `web/routes/settings.py`；运行期依赖的扩权由 `tests/test_runtime_deps.py` 拦 |
| 改图片附件的类型 / 上限 / 渲染 | `avid/attachments.py` 是单点（前端同口径的数字在 `web/src/state/imagePrep.ts`）；各家运输形状在 `providers/{openai_compat,anthropic,responses}.py`；读侧端点在 `web/routes/sessions.py` |
| 加一个事件 | `agent/events.py`（唯一单点）；同时在 `web/src/events/types.ts` 的 `EVENTS` 块里加同名成员——`tests/test_event_contract.py` 拦住两侧漂移 |

## 前端

单点声明见 [web.md](web.md)。与内核边界的契约由 `tests/test_wire_contract.py` 与 `tests/test_event_contract.py` 双侧钉住。

## 安全

| 要改什么 | 动哪里 |
|---|---|
| 调毁灭级名单 | `security/action.py` 的 `DENY_PATTERNS` |
| 改权限阈值 / 询问文案 | `security/engine.py`（默认直接跑；毁灭级问一次；凭据硬拒；full 模式跳过询问） |
| 改沙箱掩蔽 / 组装 | `security/sandbox.py`（会话目录在掩蔽名单里） |
| 改装配面 | `security/permission.py` 是唯一装配点 |
