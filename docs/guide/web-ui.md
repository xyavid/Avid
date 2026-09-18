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
| 导航列（`/sessions` 左侧） | `GET /api/sessions`、`POST /api/sessions`、`PATCH /api/sessions/{id}`、`DELETE /api/sessions/{id}` | 查询流（TanStack Query） |
| 会话时间线（`/sessions/{id}`） | `GET /api/sessions/{id}/entries`（分页）、`GET /api/runs/{id}/events`（SSE） | 历史来自条目（权威），实时来自事件 |
| 提交一次运行（输入条） | `POST /api/sessions/{id}/runs` | 命令流 → 201 `{run_id}` |
| 停止 | `POST /api/runs/{id}/cancel` | 命令流；在下一个检查点生效 |
| 审批队列（时间线内） | `GET /api/runs/{id}/approvals`、`POST /api/runs/{id}/approvals/{aid}` | 命令流 + 事件流（`approval_requested`/`approval_resolved`） |
| 运行状态条 / 对账 | `GET /api/runs/{id}` | 权威终止以注册表 + 已提交条目为准 |
| 检查器（全文 / diff / 原始 JSON） | 不新增接口 | 就地切换，不进 URL 历史 |
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

## 4. 验证

```bash
# 内核侧
uv run pytest -q                                   # 全部（含事件契约 A7 与边界 A1–A12）
uv run pytest -q tests/test_run_events.py          # B1/B2/B3 事件序列、游标补齐、resync
uv run pytest -q tests/test_approvals.py           # B4–B7 审批挂起/幂等/超时/取消
uv run pytest -q tests/test_web_api.py             # B10–B16 端点契约、分页、SSE 分帧
uv run pytest -q tests/test_web_boundaries.py      # A1–A6/A10–A12 grep 门禁

# 前端侧
pnpm -C web run check:layers                       # 网络出口唯一、feature 不互相 import
pnpm -C web run check:tokens                       # 字体声明即加载、层级只用 --z-*
pnpm -C web run lint                               # 样式/token/裸元素/i18n key 完整性
pnpm -C web test                                   # reducer / coalescer / SSE 解析单测
pnpm -C web build && pnpm -C web run gate:size     # 体积与纹理门禁

# 浏览器（需先 pnpm -C web exec playwright install chromium）
AVID_E2E=1 pnpm -C web test:e2e                    # 14 项：首屏 / 路由 / 会话流程 / 布局回归

# 手验
curl -s localhost:8765/api/meta | head -c 300
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' localhost:8765/api/nope   # 404 application/json
```

## 5. 明确未做（与本轮范围对应）

- **流式（F3）**：内核仍是整轮返回，`features.deltas = 0`，前端因此不订阅 delta；
  协议与合并器已就位，接上 `ai/client.stream_completion` 后只需把特性表打开。
- **会话分支 / fork 视图（F4）**：`features.branches = 0`；条目视图只跑 `main` 分支。
- **前端写文件 / Web 终端 / 桌面壳**：不做（§5.5）；人类要改文件或改任务状态，走
  `/api/runs` 让 agent 调用工具，权限闸门与审计因此不被绕过。
- **Playwright 用例**（a11y / 视觉回归 / 键盘 / 降级）：脚手架在 `web/e2e/`，需要
  `pnpm -C web exec playwright install chromium` 与 `AVID_E2E=1` 才跑。已落地的是
  首屏、四条路由、提交→审批→完成、检查器与 `e2e/layout.spec.ts`（输入条始终在视口内、
  会话区域独立滚动、其他工作面不溢出）；a11y / 视觉回归 / 降级仍只有约定没有用例。

## 6. 布局与交互约定（别改回去）

外壳的高度链是**视口高度**：`AppShell` 外层 `h-dvh overflow-hidden`，内层行 `h-full min-h-0`，
再往下每一级 flex 容器都带 `min-h-0`。这样「时间线是唯一滚动容器」才成立：
`ConversationView` 的 `h-full` 有确定的高度参照，输入条永远留在视口内，页面自身不滚动。

用 `min-h-screen` 代替 `h-dvh` 会让容器高度由内容决定，`flex-1` / `h-full` 全部失去参照，
消息区会把整页撑高、输入条被推到视口之外（`web/e2e/layout.spec.ts` 就是这条的回归用例）。
非会话工作面（任务板 / 技能目录 / 设置）由各自的 route 容器 `scroll-area` 承担滚动。

交互反馈同理只有一处定义：**行动型按键本身就有方框**（时间线的「复制文本 / 查看原始
JSON」、工具卡收起、处理中展开、任务卡展开、审批原因、关闭检查器、导航折叠、会话项删除），
用的是与「改名」相同的 `secondary` 样式——墨线边（`--stroke-hair`）、`--sketch-r-chip` 圆角、
纸卡底、`--sticker-2` 档硬阴影。悬停由 `ui/sketch.css` 统一抬升一档（`--sticker-3`），
按压位移同步改成新档偏移、按住时阴影归零；键盘聚焦有全局 `:focus-visible` 焦点环；
禁用保留方框但不抬升、不位移。`ghost` 只剩标题/链接型（会话标题）用，保持无框；
时间线动作的 `opacity` 只决定何时显形，方框在静止时就已存在。

所有取值来自 `ui/tokens.css`，所以「换主题」与「整体缩放」都不需要改组件。
回归用例：`web/e2e/interaction.spec.ts`。

消息同理：用户消息与模型回复是**同一族对话框**（4px 墨框 + 手绘形状 + `--sticker-4`
硬阴影，照 purrcat 的对话框外壳语言），只有方向与角色标记不同；模型卡片的角色标记是
一枚静态手绘小标记（`ui/sketch/AvidMark.tsx`，粗笔画 + 非缩放描边 + −2deg 倾斜，
`aria-hidden`）。形状按条目序号在 1→2→3 之间轮换，序号按全部条目计算，所以「加载更早」
不会让已有卡片换形。回归用例：`web/e2e/messages.spec.ts`。
