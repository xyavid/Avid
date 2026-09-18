# web/ — Avid 前端

会话时间线、审批队列、任务板（只读）、技能目录与设置。设计依据：
`docs/design/frontend-architecture.md`；使用与接口对应关系：`docs/guide/web-ui.md`。

## 命令

```bash
pnpm install
pnpm dev            # Vite（代理 /api → 127.0.0.1:8765），需另起 `uv run avid web`
pnpm build          # tsc -b + vite build → dist/
pnpm run copy:dist  # dist → ../src/avid/web/static + .build.json 构建戳
pnpm test           # vitest：reducer / coalescer / SSE 解析（纯函数，无浏览器）
pnpm run check:layers   # A8：网络出口唯一、feature 不互相 import、store 写入口收敛
pnpm run check:tokens   # C22：字体声明即加载、z-index 只用 --z-*
pnpm lint               # A9/C11/C14/C15/C18：token、裸元素、i18n key 完整性、空 catch
pnpm run gate:size      # C1/P9/C17：体积预算 + 显式豁免 + 字体/纹理字节
pnpm run verify         # 以上全部（不含 build 与 e2e）
AVID_E2E=1 pnpm test:e2e    # Playwright 14 项（需先 pnpm exec playwright install chromium）
```

## 布局的高度链

`AppShell` 外层 `h-dvh overflow-hidden`、内层行 `h-full min-h-0`，往下每级 flex 容器都带
`min-h-0`：只有高度链确定，「时间线是唯一滚动容器」与「输入条常驻视口内」才成立。
改回 `min-h-screen` 会让内容撑高整页（回归用例 `e2e/layout.spec.ts`）。

## 分层（单向依赖，越往上越知道业务）

| 层 | 目录 | 约束 |
|---|---|---|
| L0 | `ui/tokens.css`、`ui/sketch.css`、`ui/primitives/`、`ui/sketch/` | 不得出现业务名词；六种形状与八个阴影档只在这里定义 |
| L1 | `ui/patterns/` | 有形状无状态：只接受 props，不读 store、不发请求 |
| L2 | `features/*` | 一个业务面一个目录；**feature 之间不得互相 import**（跨 feature 经 store 或 route 组合） |
| L3 | `layouts/` | 三栏骨架、断点、键盘图；只依赖界面域 |
| L4 | `routes/` | URL ↔ feature 组合；**唯一**允许把查询结果与活动域拼起来的地方 |

四条机械规则（`scripts/check-layers.mjs` / `check-style.mjs` 会失败）：

1. `fetch(` / `EventSource(` / `new WebSocket(` 只能出现在 `src/api/`；
2. `features/a` import `features/b` 即失败；
3. `runStoreActions` 只允许被 `src/state/`、`src/events/`、`src/routes/` import；
4. 颜色/间距/形状/层级字面量只能出现在 `ui/tokens.css`；`dark:`、Tailwind 内建调色板类、
   `shadow-[…]`、`z-[…]`、内联 `borderRadius`/`fontFamily`、裸 `<button>/<input>/<select>`
   （`ui/` 之外）、`transition-all`、JSX 内联文案、空 `catch` 全部报错。

## 消息卡片与手绘小标记

时间线里用户消息与模型回复是**同一族对话框**（`ui/patterns/EntryRow.tsx` 用 `sketch-card`：
4px 墨框 + 手绘形状 + `--sticker-4` 硬阴影；方向相反、角色标记不同，照 purrcat 的对话框
外壳语言收敛到消息尺寸）。形状由 `ui/sketch/shapes.ts` 的 `shapeFor` 按条目序号轮换，
序号按全部条目算，加载更早不会让已有卡片换形。模型卡片的角色标记是 `ui/sketch/AvidMark.tsx`
的一枚静态手绘 SVG（粗笔画 + 非缩放描边 + −2deg，`aria-hidden`）。

## 交互反馈（按键一律有方框）

**行动型按键本身就有方框**，与「改名」等次级按钮同族：`variant="secondary"`（时间线的
复制文本 / 查看原始 JSON、工具卡收起、处理中展开、任务卡展开、审批原因、关闭检查器、
导航折叠、会话项删除）走 `ui/primitives/Button.tsx` 的同一套 `sketch-chip`——墨线边
（`--stroke-hair`）、`--sketch-r-chip` 圆角、纸卡底、`--sticker-2` 档硬阴影。
悬停由 `ui/sketch.css` 统一抬升一档（`--sticker-3`）并把按压位移同步改成新档偏移，
按住时阴影归零；键盘聚焦有全局 `:focus-visible` 焦点环；禁用保留方框但不抬升、不位移。

`variant="ghost"` 只剩「标题/链接型」用途（会话列表里的会话标题）：整块卡片点进去，
加框反而像按钮套按钮，所以它保持无框。时间线动作的 `opacity-0 → 100` 只决定
「什么时候显形」，方框与高度档在静止时就已在 DOM 里。

取值只来自 `ui/tokens.css`，所以换主题与整体缩放不需要动组件。回归用例：
`e2e/interaction.spec.ts`。

## 状态三域

| 域 | 机制 | 权威来源 |
|---|---|---|
| 权威域 | TanStack Query（`api/queries.ts`） | `session/` 文件与 `tools/tasks.py` |
| 活动域 | `state/runStore.ts` + `events/reducer.ts`（纯函数）+ `events/coalescer.ts` | 事件流（与权威域最终一致） |
| 界面域 | `state/uiStore.ts`（localStorage） | 用户偏好 |

事件绝不写进查询缓存；服务端对象身份（`entry_id` / `run_id` / `approval_id`）一律服务端生成。

## 契约

`src/events/types.ts` 的联合类型成员集合必须与 `src/avid/runtime/events.py` 的
`EVENT_TYPES` 相等——由 `tests/test_event_contract.py` 机械检查（A7）。
REST 的 TS 形状在 `src/api/types.ts`，与 `src/avid/web/schemas.py` 一一对应；将来事件
数量超过 25 个或单次要改 ≥3 个事件载荷时，改用 OpenAPI 生成到 `src/api/generated/`
（触发条件见设计文档 §6.3/§16）。
