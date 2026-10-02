# Web 服务与 API

Avid 的本地 Web 服务：内核与浏览器之间**交换什么**——端点、载荷、事件分档、错误与信任边界。
接口面设计依据见 `docs/design/frontend-architecture.md`（该文件现在只承载内核 ↔ 浏览器接口面）。
页面与视觉设计随阶段 33 在 `refactor/web-hana-ui` 分支重建（纸本视觉对话界面，源码在
仓库根 `web/`）；页面结构以该分支的 `web/src/surfaces/` 为准，本文件仍只描述内核 ↔
浏览器交换什么。

## 1. 启动

```bash
# 1) 内核 + Web 依赖
uv sync --extra web

# 2) 起服务（API + SSE + 静态资源）
uv run --env-file .env avid web --port 8765
# → http://127.0.0.1:8765

# 可选：指定这个进程绑定的工作地点（缺省就是当前目录的）。**启动不写盘**：
# 注册表只由 `avid workspace add` / POST /api/workspaces 改。新建会话不带 workspace
# 时一律 400 workspace_required，绑定值只做预选。
uv run --env-file .env avid web --port 8765 --workspace /path/to/project
```

前端源码在仓库根 `web/`（React 18 + Vite + pnpm）；构建产物经 `pnpm -C web run copy:dist`
交付到 `src/avid/web/static/`，由本服务托管。没有产物时访问任何非 `/api` 路径会得到
HTTP 503 `static_missing`（「前端尚未构建」）。API 与 SSE 不受影响。

`GET /api/meta` 的 `build` 字段返回构建戳（`git_sha` + `built_at`）；没有产物时这个
字段没有戳值。

## 2. 端点与数据

| 方法与路径 | 语义 | 数据 / 字段 | 主要错误 |
|---|---|---|---|
| `GET /api/meta` | 版本、特性表、能力面与构建戳 | `api_version`；`features`（`deltas` / `branches` / `usage` / `workspace_picker` / `workspace_delete` / `full_access` 等——客户端按特性分支，不按版本号分支）；`event_types`；`capabilities`（`tools` / `skills` / `model` / `known_models` / `models`（BYOK 候选，`ref`=`providerId/modelId`） / `workspace` / `workspace_picker` / `sandbox`）；`stream`（`heartbeat_seconds` / `terminal_fallback_seconds` / `replay_buffer_size`）；`build`（`git_sha` / `built_at`） | — |
| `GET /api/sessions` | 会话列表（元信息 + 条数 + `active_run_id`） | 名字是会话文件里的一个值、条数要读全部条目，所以这个端点当前是 O(会话数 × 文件大小) | — |
| `POST /api/sessions` | 新建会话 | `{id?, name?, workspace}`；`workspace` 必填，缺 → 400 `workspace_required`（进程绑定的工作地点只做预选）；未知字段 422（`extra="forbid"`） | 400 / 409 已存在 |
| `GET /api/sessions/{id}` | 元信息 + 统计 + `active_run_id` + `branch` | | 404 |
| `PATCH /api/sessions/{id}` | 改名（= 值写入） | `{name}` | 404 |
| `DELETE /api/sessions/{id}` | 销毁（要求无活动 run） | 204 | 404 / 409 |
| `GET /api/sessions/{id}/entries` | 条目分页 | `branch`（默认 `main`）/ `order`（`asc`\|`desc`，默认 `desc`）/ `limit` / `cursor_seq`；响应带 `has_more`、`next_cursor`、`truncated_tail` | 404 |
| `GET /api/sessions/{id}/branches` | 分支列表 | `name` / `tip_entry_id` / `entry_count` / `is_default` / `usage`（该分支最近一次运行的用量快照，按**分支**记账） | 404 |
| `POST /api/sessions/{id}/branches` | 从某个条目开一条命名分支 | `{name?, at?}`；`at` = 分叉点条目 id，缺省 = 从零开一条空分支；未给名字时自动取 `b2`、`b3`…（跳过已占用的）；201 | 400 `invalid_request`（分叉点未知）/ 409 `branch_exists`（重名）/ 409 `session_busy`（活动 run 期间拒绝分叉） |
| `POST /api/sessions/{id}/runs` | 起一次运行 | `{prompt, auto_approve?, branch?, permission?, full_access_ack?}`；`branch` 默认 `main`，决定接哪条链尾；`permission` 缺省取会话所属工作区的默认权限（再缺省才是 `manual`）；201 `{run_id, session_id, status}` | 409 已有活动 run / 422（`full` 缺 `full_access_ack`） |
| `GET /api/runs/{run_id}` | 运行状态与统计 | `status` / `round` / `tokens` / `usage` / `error` / `cancel_requested` / `pending_approvals`；权威终止以运行注册表 + 已提交条目为准 | 404 |
| `GET /api/runs/{run_id}/events` | **SSE**，支持 `Last-Event-ID` 与 `?after=`；`?deltas=1` 才投递 delta | 分档见 §3；并发上限 24 条，见 §2.1 | 404 / 503 `too_many_streams` |
| `POST /api/runs/{run_id}/cancel` | 请求取消：**在下一个检查点生效**，不承诺立即停止 | 202 `{run_id, status, cancel_requested}` | 404 / 409 已结束 |
| `GET /api/runs/{run_id}/approvals` | 当前待决审批 | 刷新与第二个标签页靠它恢复（SSE 重放也会重发 `approval_requested`） | 404 |
| `POST /api/runs/{run_id}/approvals/{aid}` | 答复 | `{decision: "allow"｜"deny"}`；重复投递返回 200 `{accepted:false, already}`，**不二次批准** | 404 / 409 已决 / 410 已过期 |
| `GET /api/workspaces` | 已登记的工作区候选列表 | `id` / `root` / `name` / `default_permission` / `created_at` / `last_used_at` / `is_default` | — |
| `POST /api/workspaces` | 登记一个工作区（写 `~/.avid/workspaces.json`） | `{path, name?, permission?}`；`permission` 只有 `manual` / `auto`——工作区默认权限不接受 `full`，非法值 422 | 409 `workspace_exists`（含进程绑定的那个）/ 400 `workspace_invalid`（路径不存在） |
| `POST /api/workspaces/pick` | 由**服务端**在宿主机弹一次文件夹选择器 | `{path}`；`path: null` = 用户取消（不是错误）。浏览器拿不到目录的绝对路径（`webkitdirectory` 只给相对路径、File System Access API 只给 handle），所以这一步只能由跑在本机的后端做；它按 `AVID_PICKER_CMD` → tkinter → zenity/kdialog → Windows（WSL 互操作）→ osascript 依次探测，`capabilities.workspace_picker` 报告实际用的是哪一个（`null` = 这台机器没有可用的） | 409 已有对话框开着 / 503 没有可用后端（消息里给出 `avid workspace add <路径>`） |
| `DELETE /api/workspaces/{id}` | 从候选列表里摘掉一项，**不删会话数据**（目录、`.avid/sessions` 与会话文件一行不改） | 204 | 409 `workspace_bound`（想摘的是进程绑定的那个，它永远在候选里） |
| `GET /api/settings/byok` | BYOK 配置全量（providers + chat 绑定 + 每家 `key_set`） | **密钥只入不出**：任何响应不回传明文；`legacy` 块仅在没有 BYOK 文件时返回（界面拿它预填「导入旧配置」草稿） | — |
| `PUT /api/settings/byok` | 整体保存（providers 全量 + `{chat: "providerId/modelId"\|null}`） | 载荷里的 `api_key`（只入）剥出写进 `~/.avid/secrets.json`（0600，引用缺省 = provider id），配置文件只留引用；validate 不过 → 400 `invalid_request`，**不落盘也不留半份密钥**；保存后 `resolve_chat` 每次运行重读，下一条消息立即生效 | 400 / 422 |
| `POST /api/settings/byok/test` | 两步连通校验（① `max_tokens=1` 最小对话；② 必答 `get_time` 工具冒烟） | 针对**载荷**而非已保存配置：保存前就能测，密钥走载荷不落盘；失败按 401/403 → 密钥、404 → `base_url` 少 `/v1`、429 → 限流、超时 → 不可达、空 `tool_calls` → 不支持工具 分类 | — |
| `DELETE /api/settings/byok` | 删配置与密钥两份文件，回落 env / 旧 `model.toml` | 204 | — |
| `GET /api/skills` | 技能目录（name + 一行描述，与 system prompt 同源） | | — |
| `GET /api/health` | 就绪探针 | `status` / `api_version` / `uptime_ms` | — |

未知 `/api/*` 一律返回 JSON 404，**不回落到 SPA 外壳**。

**分支是命名的链尾**：一个值 `avid.branch.tip.<name>` 指向某条条目，链本身由 `parent_id`
还原。因此分叉不复制条目——新链与旧链在分叉点之前是同一批条目。服务端没有「当前分支」，
只有一组链尾值，`branch` 由调用方在每次读条目、起运行时给出。

**`permission` 是三轴预设**（阶段 26），不是一道信任边界的三个刻度：

| 档 | 值 | approval | sandbox | network |
|---|---|---|---|---|
| 手动 | `manual` | 问人 | 工作区 | 无出网 |
| 自动 | `auto` | 确定性分类器（判不准即拒） | 工作区 | 无出网 |
| 完全访问 | `full` | 不问 | **已禁用** | 不限 |

- 优先级：本次请求的 `permission` > 工作区默认权限（`avid workspace permission <id> <mode>`）> `manual`。
- `full` 必须带 `full_access_ack: true`，缺了服务端 422；显式授权是请求体的一部分，不是客户端界面的一部分。
- `manual` 与 `auto` 的沙箱逐字相同：沙箱能保证的区内常规动作不问（否则 approval 与 sandbox 就退化成一件事）；越界、危险命令、`.env` 这类 ask 档、`subagent` 才会进入 REVIEW。
- 沙箱是否可用是服务端实测的事实：`GET /api/meta` 的 `capabilities.sandbox` 带 backend / available / network / reason / landlock_abi。

**工作区**是一个本地目录，同时是权限边界、会话归属与干活的地点：

- 进程绑定的工作地点**不写进注册表**，所以注册表里有什么只取决于登记过什么，不取决于起过几次服务。
- 会话的归属是**创建时的静态事实**，写在会话 header 里；注册表被删掉也不影响已有会话的归属查询。
- 工作区的默认权限只能取 `manual` / `auto`：`full` ≠ default，它在 DTO 类型、CLI choices 与注册表三处都不存在。

**用量读数**（`features.usage` 声明时才存在，口径单点在 `RunState.usage_report()`）：

- 占用用 provider 上报的真实 `prompt_tokens`，不是本地估算；会话头部的累计 `tokens`（这次运行一共花了多少）与这里的「现在占了多少」是两个数。
- 窗口按 `AVID_CONTEXT_WINDOW` → 内置模型名小表 → 问一次 provider 的 `/models`（`context_length` / `max_model_len` 等；进程内缓存、失败静默、`AVID_MODEL_INFO=off` 关掉）依次取值；三条都没有就只报 tokens、不给百分比。显式配置写成 `128k` 这类非数字会直接报错，不静默回落。
- 系统提示词 / 工具定义 / 对话消息三块是**估算**：系统提示词与工具定义从不发给前端（前端只有对话条目），所以内核在发请求前记下三者的字符数，再由 `usage_report()` 按字符占比把真实的 `prompt_tokens` 分给三块——三块之和恰好等于真实总数（余数归对话消息）；没有分块数据时（旧快照、这一轮没记字符数）这三块不出现。
- 缓存以 provider usage 为准：OpenAI 的 `prompt_tokens_details.cached_tokens`、DeepSeek 的 `prompt_cache_hit_tokens`、Anthropic 的 `cache_read_input_tokens` / `cache_creation_input_tokens`、Gemini 的 `cachedContentTokenCount` 都会归一化；这家没有写入缓存计数（OpenAI 系）时那一项不出现，不当成 0。命中率分母是含命中部分的输入总量。
- 压缩后读数是压缩发生**之后**下一轮的真实 `prompt_tokens`；压缩后没再调用模型就没有这一项。
- 端点不认 `stream_options.include_usage` 时整块读数缺失。
- 实时值随每轮 `run_status` 事件到达；落盘值随 `GET /api/sessions/{id}/branches` 的 `usage` 给该分支最近一次运行的读数。

## 2.1 信任边界（这不是鉴权，但也不是"随便谁都能打"）

服务默认只监听回环，且带一条极薄的信任边界（`web/app.py` 的
`TrustBoundaryMiddleware`）。它挡的是两类**浏览器替别人发请求**的路，不是账号体系：

| 攻击面 | 机制 | 表现 |
|---|---|---|
| DNS rebinding | 恶意域名解析到 `127.0.0.1`，浏览器认为它同源 | `Host` 不在白名单 → **400 `host_rejected`** |
| CSRF | 跨站表单/`fetch` 的"简单请求"不做预检就能打到无 body 的写端点（`POST /api/workspaces/pick` 会在宿主机弹文件夹选择器、`POST /api/runs/{id}/cancel` 会取消任务） | `Origin` 不在白名单 → **403 `origin_rejected`** |

白名单默认是 `127.0.0.1` / `localhost` / `::1`（比主机名，不比端口）。
命令行（curl）与测试不带 `Origin`，因此不受影响。非回环部署（`--host 0.0.0.0`）
会打印警告，并把绑定的主机名与本机地址加进白名单；要额外放行域名/反代主机就设
`AVID_ALLOWED_HOSTS=avid.internal,10.0.0.5`（逗号分隔）。

事件流还有一个并发上限：**同时最多 24 条**（`svc.MAX_CONCURRENT_STREAMS`）。同步
生成器的 `next()` 会阻塞到下一个事件或心跳，一条连接因此长期占住线程池里的一个
线程；超过上限的连接立刻收到 503 `too_many_streams`，而不是把 REST 请求拖慢。
这是"把占用封顶"，不是异步化——本地单用户场景下，一两个标签页远够用。

**仍然要说清的边界**：这是单用户本地工具，没有认证。能连上这个端口的人就能建会话、
跑命令（命令以本进程权限执行）。所以不要把 `--host` 指到公网；需要多人/远程使用时
应当放在带认证的反向代理之后，而不是直接暴露。

## 3. 事件分档（消费规则）

| 档 | 是否带 `id`/`seq` | 是否重放 | 消费规则 |
|---|---|---|---|
| durable | 是 | 是 | 按 `(run_id, seq)` 幂等去重；终止类事件之前，待处理的 delta 必须先收敛 |
| transient | 否 | 否 | 只更新轮次 / token / 活动工具等状态 |
| delta | 否 | 否 | 默认不投递（`?deltas=1` 才订阅）；可任意丢，最终内容由 durable 给全 |

终止类事件（`run_finished` / `run_failed` / `run_cancelled`）渲染前 **cancel** 待处理
delta，durable 事件渲染前 **flush**——两者不混用：flush 是「把已有内容落上去」，
cancel 是「别让旧内容盖住最终结果」。

客户端按 `GET /api/meta` 的 `features` 分支、不按版本号分支：`features.deltas = 1` 时才
带 `?deltas=1` 订阅。**delta 不落盘、不重放**，所以刷新后正在流式的那一轮会以「等待中」
出现，随后由 durable 补齐（内核只把 delta 推给当场订阅的消费者，`?deltas=1` 的帧不写
`id:` 行，浏览器重连自然停在最后一个 durable 点）。

> **§3.1–§3.3 已于阶段 32 随旧前端一并删除。** 分支视图、工作区与权限模式、用量指示器
> 这三节的主体是界面行为与呈现；其中的接口事实（端点名、字段名、事件名、状态码）已并入 §2。

## 4. 验证

```bash
# 内核侧
uv run pytest -q                                   # 全部（含事件契约与边界 grep 门禁）
uv run pytest -q tests/test_run_events.py          # 事件序列、游标补齐、resync、delta 通道
uv run pytest -q tests/test_llm.py                 # 流式与非流式逐字段等价
uv run pytest -q tests/test_approvals.py           # 审批挂起/幂等/超时/取消
uv run pytest -q tests/test_web_api.py             # 端点契约、分页、SSE 分帧、分支端点
uv run pytest -q tests/test_branches.py            # 分叉语义：前缀共享、分支隔离、活动 run 拒绝
uv run pytest -q tests/test_web_boundaries.py      # grep 门禁：内核不 import 框架、web 不写会话

# 手验
curl -s localhost:8765/api/meta | head -c 300   # 其中的 build.git_sha 是那种产物的提交戳
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' localhost:8765/api/nope   # 404 application/json
```

**前端门禁（阶段 33 收口恢复）**：`pnpm -C web run verify` = typecheck + vitest + build +
`gate:size`（体积预算 `web/budget.json`）；两侧对账由 `tests/test_wire_contract.py`、
`tests/test_event_contract.py` 与模式词表第四处（`test_modes.py`）守着。**浏览器 e2e
尚未重建**：旧 Playwright 用例已随前端清空删除，界面验收走实机 CDP 脚本（`dev/evidence/`）；
a11y 与视觉回归门禁也还没有，重开信号见 `docs/status/ROADMAP.md`。

> **§5–§6 已于阶段 32 随旧前端一并删除。** §5 是与当时范围对应的「明确未做」清单（成本与
> 延迟台账、前端写文件 / Web 终端 / 桌面壳、流式渲染库、Playwright 用例），§6 是布局与交互
> 约定——都是那份已被删除的前端的内部设计与呈现约定。
