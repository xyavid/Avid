# Web 界面与 API

Avid 的浏览器界面：会话时间线、审批队列、任务板（只读）、技能目录、设置。
设计依据见 `docs/design/frontend-architecture.md`；本文件只写**怎么用**与**接口对应关系**。

## 1. 启动

```bash
# 1) 内核 + Web 依赖
uv sync --extra web

# 2) 起服务（API + SSE + 静态资源）
uv run --env-file .env avid web --port 8765
# → http://127.0.0.1:8765

# 可选：把这个进程绑到一个工作区（单工作区模式）。缺省是多工作区模式——
# 界面上的工作区选择器列出 `avid workspace list` 里的候选，新建会话必须选一个。
uv run --env-file .env avid web --port 8765 --workspace /path/to/project
```

开发期（前端热更新）两个进程：

```bash
uv run --env-file .env avid web --port 8765     # 终端 A：API 与事件流
pnpm -C web dev                                 # 终端 B：Vite，代理 /api → 8765
# → http://127.0.0.1:5173
```

交付形态（一个进程、安装者不需要 Node）：

```bash
pnpm -C web install
pnpm -C web build
pnpm -C web run copy:dist        # dist → src/avid/web/static + 构建戳
uv run avid web --port 8765      # 静态资源与 API 同源
```

`GET /api/meta` 的 `build` 字段返回构建戳（`git_sha` + `built_at`），UI 的「设置」
页会显示它；从 checkout 直接跑而没有产物时显示「从 checkout 运行（未打包产物）」。

## 2. 页面与接口对应关系

| 页面 / 交互（URL） | 主要接口 | 数据流 |
|---|---|---|
| 导航列（`/sessions` 左侧） | `GET /api/sessions`、`POST /api/sessions`、`PATCH /api/sessions/{id}`、`DELETE /api/sessions/{id}` | 查询流（TanStack Query）；列表项带归属工作区 |
| 工作区选择器（新建会话前） | `GET /api/workspaces`、`POST /api/workspaces` | 查询流；选中的 id 随 `POST /api/sessions` 发出（多工作区模式下必填，缺了是 400 `workspace_required`） |
| 权限模式选择器（输入条旁） | 不新增接口 | 随 `POST /api/sessions/{id}/runs` 的 `permission` 发出；缺省取会话所属工作区的 `default_permission` |
| 会话时间线（`/sessions/{id}`） | `GET /api/sessions/{id}/entries`（分页，带 `branch`）、`GET /api/runs/{id}/events`（SSE） | 历史来自条目（权威），实时来自事件 |
| 提交一次运行（输入条） | `POST /api/sessions/{id}/runs`（`branch` 决定接哪条链尾） | 命令流 → 201 `{run_id}` |
| 分支选择器（会话头部下方） | `GET /api/sessions/{id}/branches`、`POST /api/sessions/{id}/branches` | 查询流 + 命令流；「切换」只是本地选择——服务端没有「当前分支」，它只有一组链尾值 |
| 从此处分支（条目动作行） | `POST /api/sessions/{id}/branches` `{at: entry_id}` | 命令流；成功后自动切到新链 |
| 停止 | `POST /api/runs/{id}/cancel` | 命令流；在下一个检查点生效 |
| 审批队列（时间线内） | `GET /api/runs/{id}/approvals`、`POST /api/runs/{id}/approvals/{aid}` | 命令流 + 事件流（`approval_requested`/`approval_resolved`） |
| 运行状态条 / 对账 | `GET /api/runs/{id}` | 权威终止以注册表 + 已提交条目为准 |
| 头部三个工具按钮 | 不新增接口 | 回到最新（时间线贴底）／开关检查器／跳到待决审批（有待决项时带计数徽标并脉冲） |
| 检查器（全文 / diff / 原始 JSON） | 不新增接口 | 就地切换，不进 URL 历史 |
| 导航列（收起 / 展开） | 不新增接口 | 收起为 64px 图标轨；一级切换只有这一处入口 |
| 任务板（`/tasks`） | `GET /api/tasks`、`GET /api/tasks/{id}` | 只读：任务写入者只有 agent 的任务工具 |
| 技能目录（`/skills`） | `GET /api/skills` | 与 system prompt 同源 |
| 设置（`/settings`） | `GET /api/meta`、`GET /api/health` | 只读；改配置仍走 CLI / 环境变量 |

健康探针：`GET /api/health`。未知 `/api/*` 一律返回 JSON 404，**不回落到 SPA 外壳**。

## 3. 事件分档（前端消费规则）

| 档 | 是否带 `id`/`seq` | 是否重放 | 前端怎么处理 |
|---|---|---|---|
| durable | 是 | 是 | 按 `(run_id, seq)` 幂等去重；渲染前 flush 待处理 delta |
| transient | 否 | 否 | 只更新轮次 / token / 活动工具等状态 |
| delta | 否 | 否 | 默认不投递（`?deltas=1` 才订阅）；rAF 合并，≤1 次提交/帧 |

终止类事件（`run_finished` / `run_failed` / `run_cancelled`）渲染前 **cancel** 待处理
delta，durable 事件渲染前 **flush**——两者不混用（不变量 I12）。

客户端按 `GET /api/meta` 的 `features` 分支、不按版本号分支：`features.deltas = 1` 时
`useRunStream` 才带 `?deltas=1` 订阅，delta 经 rAF 合并器落到一个乐观条目上，随后的
durable `assistant_message` 带完整内容并把它替换掉。**delta 不落盘、不重放**，所以刷新
后正在流式的那一轮会以「等待中」出现，随后由 durable 补齐（内核只把 delta 推给当场
订阅的消费者，`?deltas=1` 不写 `id:` 行，浏览器重连自然停在最后一个 durable 点）。

## 3.1 分支（F4）

分支是**命名的链尾**：一个值 `avid.branch.tip.<name>` 指向某条条目，链本身由
`parent_id` 还原。因此分叉不复制条目——新链与旧链在分叉点之前是同一批条目。

- 「切换分支」只改前端的 `branch` 状态并重取该链的条目（服务端不知道谁在「当前」分支）。
- 「从此处分支」= `POST /branches {at: entry_id}`，之后 `POST /runs {branch}` 让这次
  运行接在新链尾上。`features.branches = 1` 时才显示这些入口。
- 活动 run 期间服务端拒绝分叉（409 `branch_exists` / `session_busy`），界面先把入口禁用。
- 重名分支 409 `branch_exists`；未知分叉点 400 `invalid_request`；未给名字时自动取
  `b2`、`b3`…（跳过已占用的）。

## 3.2 工作区与权限模式（阶段 18）

**工作区**是一个本地目录，同时是权限边界、会话归属与干活的地点。界面上它出现在两处：
新建会话前必须选（列表来自注册表，`is_default` 或最近使用的那一个被预选），
会话卡与详情显示归属名字。归属是**创建时的静态事实**，写在会话 header 里，
所以注册表被删掉也不影响已有会话的归属查询。

**权限模式**决定"哪些动作会打问号"，三档是信任边界：

| 档 | 值 | 行为 |
|---|---|---|
| 严格 | `strict` | 每个受管动作都要问（默认） |
| 工作区 | `workspace` | 区内常规操作免问；危险命令仍问；越界需同意一次 |
| 系统级 | `system` | 默认免问；仅危险命令仍问 |

- 优先级：本次请求的 `permission` > 工作区默认权限（`avid workspace permission <id> <mode>`）> `strict`。
- 越界（工作区之外的目标）在严格与工作区档都会问一次，同意后**本次运行内**不再问同一目标；
  系统级档直接放行。
- 危险命令（提权、递归删除、系统包管理、`curl | sh` 等）在三档里都要问，理由会写明类别；
  硬拒绝清单（`rm -rf /` 这类不可恢复的破坏）任何档、任何回答都不放行。
- 界面上的选择**不持久化**：它是这次会话视图的瞬时状态，缺省值来自服务端。理由是
  "上次选了系统级，下次打开浏览器继续全放行"属于安全默认值问题。

## 4. 验证

```bash
# 内核侧
uv run pytest -q                                   # 全部（含事件契约 A7 与边界 A1–A12）
uv run pytest -q tests/test_run_events.py          # B1/B2/B3 事件序列、游标补齐、resync、delta 通道
uv run pytest -q tests/test_llm.py                 # B9 流式与非流式逐字段等价
uv run pytest -q tests/test_approvals.py           # B4–B7 审批挂起/幂等/超时/取消
uv run pytest -q tests/test_web_api.py             # B10–B16 端点契约、分页、SSE 分帧、分支端点
uv run pytest -q tests/test_branches.py            # F4 分叉语义：前缀共享、分支隔离、活动 run 拒绝
uv run pytest -q tests/test_web_boundaries.py      # A1–A6/A10–A12 grep 门禁

# 前端侧
pnpm -C web run verify                             # layers + tokens + lint + vitest + 体积门禁
pnpm -C web run check:layers                       # 网络出口唯一、feature 不互相 import
pnpm -C web run check:tokens                       # 字体声明即加载、层级只用 --z-*
pnpm -C web run lint                               # 样式/token/裸元素/i18n key 完整性
pnpm -C web test                                   # reducer / coalescer / SSE 解析单测
pnpm -C web build && pnpm -C web run gate:size     # 体积与纹理门禁

# 浏览器（需先 pnpm -C web exec playwright install chromium）
AVID_E2E=1 pnpm -C web test:e2e                    # 首屏 / 路由 / 会话流程 / 布局 / 消息卡片 / 交互反馈 / 分支

# 浏览器 + 流式：内核以 AVID_E2E_STREAM=1 起（不注入 chat，svc 因而走生产路径），
# dev/tmp/e2e_server.py 会把 stream_completion 换成「按脚本产出再分片」的假实现。
AVID_PORT=8877 AVID_E2E_STREAM=1 uv run --extra web python dev/tmp/e2e_server.py
AVID_BASE_URL=http://127.0.0.1:8877 AVID_E2E=1 pnpm -C web test:e2e
```

# 手验
curl -s localhost:8765/api/meta | head -c 300
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' localhost:8765/api/nope   # 404 application/json
```

## 5. 明确未做（与本轮范围对应）

- **前端写文件 / Web 终端 / 桌面壳**：不做（§5.5）；人类要改文件或改任务状态，走
  `/api/runs` 让 agent 调用工具，权限闸门与审计因此不被绕过。
- **流式渲染库（streamdown 类）**：不做——delta 只到「乐观条目 + rAF 合并」这一层，
  Markdown 在 durable 消息到达时整条渲染。失效信号是接入 delta 后帧率不达标。
- **Playwright 用例**（a11y / 视觉回归 / 键盘 / 降级）：脚手架在 `web/e2e/`，需要
  `pnpm -C web exec playwright install chromium` 与 `AVID_E2E=1` 才跑；用脚本模型跑时
  先 `pnpm -C web build`（`dev/tmp/e2e_server.py` 服务 `web/dist`），细节见 `web/e2e/README.md`。
  已落地的是首屏、四条路由、提交→审批→完成、检查器、布局（含中档抽屉）、消息卡片（含两枚
  角色标记）、交互反馈、分支旅程、流式收敛与工作区/权限选择器；a11y / 视觉回归 / 降级仍
  只有约定没有用例。

## 6. 布局与交互约定（别改回去）

**会话头部**：会话 ID 与运行 ID 是两张常驻胶带，它们在**流式布局**里（不是绝对定位）
——绝对定位时第二张胶带会压住下方的工具按钮（4 种宽度组合下都实测相交）；窄卡片下
`flex-wrap` 让胶带换行。三个工具按钮的可访问名各自独立（回到最新 / 检查器 / 待决审批），
不与时间线条目的「查看原始 JSON」混淆。

**导航列**：宽档默认 320px，收起是 64px 图标轨；**收起态头部竖排**（图标 + 图标），
因为「44px 图标 + 文字按钮」并排放不进 64px，会溢出并压到会话卡上（`e2e/layout.spec.ts`
里有几何断言）。一级切换只有导航列一处入口——⌘K 命令面板因与导航列完全重合已删除。

**中档（960–1279px）**：导航列恒定 64px 图标轨，会话列表走**左侧抽屉**（点轨道头部的
「会话列表」按钮）。没有做成「就地展开成 320px」：那会吃掉 1/3 屏宽，且展开态经
`uiStore` 持久化后会长期占用，而抽屉不改变内容宽度。抽屉里选中会话后会自己收起
（`AppShell` 的 `nav` 是收 `close` 回调的渲染函数）——否则刚选中的内容会被 75vh 的
覆盖层挡住。回归用例：`e2e/layout.spec.ts` 的「中档宽度」一条。

外壳的高度链是**视口高度**：`AppShell` 外层 `h-dvh overflow-hidden`，内层行 `h-full min-h-0`，
再往下每一级 flex 容器都带 `min-h-0`。这样「时间线是唯一滚动容器」才成立：
`ConversationView` 的 `h-full` 有确定的高度参照，输入条永远留在视口内，页面自身不滚动。

用 `min-h-screen` 代替 `h-dvh` 会让容器高度由内容决定，`flex-1` / `h-full` 全部失去参照，
消息区会把整页撑高、输入条被推到视口之外（`web/e2e/layout.spec.ts` 就是这条的回归用例）。
非会话工作面（任务板 / 技能目录 / 设置）由各自的 route 容器 `scroll-area` 承担滚动。

交互反馈同理只有一处定义：**行动型按键本身就有方框**（时间线的「复制文本 / 查看原始
JSON / 从此处分支」、工具卡收起、处理中展开、任务卡展开、审批原因、关闭检查器、导航折叠、会话项删除），
用的是与「改名」相同的 `secondary` 样式——墨线边（`--stroke-hair`）、`--sketch-r-chip` 圆角、
纸卡底、`--sticker-2` 档硬阴影。悬停由 `ui/sketch.css` 统一抬升一档（`--sticker-3`），
按压位移同步改成新档偏移、按住时阴影归零；键盘聚焦有全局 `:focus-visible` 焦点环；
禁用保留方框但不抬升、不位移。会话列表的会话标题同样用 `secondary`（与「改名 / 删除」
同族），并且 `w-full`——方框铺满卡片内容区，长会话名在框内换行而不撑破卡片。按钮变体只有 primary / secondary / danger 三种，每个都自带方框——原先无框的
`ghost` 变体在最后一个调用点也加上框之后已删除。时间线动作的 `opacity` 只决定何时显形，
方框在静止时就已存在。

所有取值来自 `ui/tokens.css`，所以「换主题」与「整体缩放」都不需要改组件。
回归用例：`web/e2e/interaction.spec.ts`。

消息同理：用户消息与模型回复是**同一族对话框**（4px 墨框 + 手绘形状 + `--sticker-4`
硬阴影，照 purrcat 的对话框外壳语言），只有方向与角色标记不同。角色标记按作者分两枚
**不同的**手绘小标记：模型是 `ui/sketch/AvidMark.tsx`（角形笔画），用户是
`ui/sketch/UserMark.tsx`（歪头 + 肩弧）。两枚共用同一套笔触契约（粗笔画 + 非缩放描边
+ −2deg 倾斜 + `aria-hidden`），但形状必须不同——标记的职责就是区分作者，两卡共用一个
形状等于没标。形状按条目序号在 1→2→3 之间轮换，序号按全部条目计算，所以「加载更早」
不会让已有卡片换形。回归用例：`web/e2e/messages.spec.ts`（含两枚标记 path 数据不相等的
断言，防止以后有人「顺手统一」成一个组件）。
