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
pnpm run gate:size      # C1/P9/C17/C18：体积预算 + 显式豁免 + CSS/字体/纹理字节（读 dist/）
pnpm run check:contrast # 按 alpha 合成算 token 表里声明的 17 对，正文 <4.8 即红
pnpm run verify         # 以上全部（不含 build 与 e2e；gate:size 依赖 dist/，先 pnpm build）
node scripts/measure-glass.mjs --base http://127.0.0.1:8877   # 手动：玻璃的长任务/帧率对照（不进 verify）
AVID_E2E=1 pnpm test:e2e    # Playwright 47 项（含 5 条视觉基线；需先 pnpm exec playwright install chromium）
```

**Node ≥ 22.22**：jsdom 30 依赖 undici 8，后者调 `node:worker_threads.markAsUncloneable`
（Node ≥ 22.19 才有）。版本不够时 `pnpm install` 会因为 `.npmrc` 的 `engine-strict`
直接失败——没有这道检查的话依赖装得下、纯函数用例也全绿，只在用 jsdom 的那条用例上留
一条 unhandled error。下限写在 `package.json` 的 `engines`；CI 与本机基线都是 24。

## 导航列

宽档默认 320px；**收起后是 64px 图标轨**，收起态的头部必须竖排——`图标 + 文字`两个按钮
并排放不进 64px，实测文字按钮会溢出轨道 44px 压到会话卡上（回归用例在
`e2e/layout.spec.ts`：轨道内每个按钮都在轨道内、且没有任何按钮与会话卡相交）。

一级切换**只有导航列一处入口**：曾经还有一个 ⌘K 命令面板，功能与导航列完全重合
（只有四个工作面、没有别的命令），判定为多余后删除。

**导航列的内容是"工作区即文件夹"**：每个工作区一个可折叠的组（`title` 行 + 它自己的
会话），组内超出 5 条时给「展开其余 N 个会话」。因此**没有"新建会话 + 工作区下拉"这一对**
——在哪个文件夹上点 ＋ 就在哪个工作区建会话，"归属"永远是点出来的那一下。分组、可见条数、
默认展开谁都在 `src/features/sessions/lib/navTree.ts`（纯函数 + vitest）；界面展开态是
每个文件夹自己的 `useState`（纯界面偏好，不进 `uiStore`）。两个按钮的可访问名必须能分辨：
折叠按钮 = 工作区名，新建按钮 = `在「<名字>」新建会话`。

右上角两个图标按钮：🔍 展开搜索（纯客户端按会话名过滤，命中项强制展开且不受预览上限
约束，查询不持久化），＋ 新增工作区（宿主机文件夹选择器）。

## 布局的高度链

`AppShell` 外层 `h-dvh overflow-hidden`、内层行 `h-full min-h-0`，往下每级 flex 容器都带
`min-h-0`：只有高度链确定，「时间线是唯一滚动容器」与「输入条常驻视口内」才成立。
改回 `min-h-screen` 会让内容撑高整页（回归用例 `e2e/layout.spec.ts`）。

## 分层（单向依赖，越往上越知道业务）

| 层 | 目录 | 约束 |
|---|---|---|
| L0 | `ui/tokens.css`、`ui/glass.css`、`ui/primitives/`、`ui/glass/` | 不得出现业务名词；玻璃面/高光边/投影三档/圆角四档只在这里定义 |
| L1 | `ui/patterns/` | 有形状无状态：只接受 props，不读 store、不发请求 |
| L2 | `features/*` | 一个业务面一个目录；**feature 之间不得互相 import**（跨 feature 经 store 或 route 组合） |
| L3 | `layouts/` | 三栏骨架、断点、导航列收起/展开；只依赖界面域 |
| L4 | `routes/` | URL ↔ feature 组合；**唯一**允许把查询结果与活动域拼起来的地方 |

四条机械规则（`scripts/check-layers.mjs` / `check-style.mjs` 会失败）：

1. `fetch(` / `EventSource(` / `new WebSocket(` 只能出现在 `src/api/`；
2. `features/a` import `features/b` 即失败；
3. `runStoreActions` 只允许被 `src/state/`、`src/events/`、`src/routes/` import；
4. 颜色/间距/形状/层级字面量只能出现在 `ui/tokens.css`；`dark:`、Tailwind 内建调色板类、
   `shadow-[…]`、`z-[…]`、内联 `borderRadius`/`fontFamily`、裸 `<button>/<input>/<select>`
   （`ui/` 之外）、`transition-all`、JSX 内联文案、空 `catch` 全部报错。

## 视觉语言：暖羊皮纸 + 液态玻璃

时间线里用户消息与模型回复是**同一族玻璃卡**（`ui/patterns/EntryRow.tsx` 用 `surface-card`：
半透明玻璃面 + 1px 高光边 + `--lift-2` 柔和投影；方向相反、角色标记不同）。
**卡片不再轮换形状**：旧语言那三种手绘圆角随涂鸦机制一起删除，`e2e/messages.spec.ts`
反过来断言「所有消息卡片同形」。角色标记是两枚 lucide 图标（`Bot` / `User`，`strokeWidth
1.75`，`aria-hidden`）。

值只有一处来源（`ui/tokens.css`），语言名不进组件（`check:style` 守）：**换视觉语言只动
L0/L1**——阶段 23b 的整个卖点。颜色的可读性由 `check:contrast` 机械守住（按 alpha 合成算），
不是靠人记得算。玻璃的代价与模糊预算见设计文档 §8.9。

## 交互反馈（按键一律有方框）

**行动型按键本身就有方框**，与「改名」等次级按钮同族：`variant="secondary"`（时间线的
复制文本 / 从此处分支、工具卡收起、处理中展开、任务卡展开、审批原因、关闭检查器、
导航折叠、会话项删除）走 `ui/primitives/Button.tsx` 的同一套 `surface-chip`——玻璃面 +
1px 高光边（`--glass-edge`）、`--r-chip` 圆角、`--lift-1` 投影。
悬停由 `ui/glass.css` 统一抬升一档投影并加一点亮度，按住时投影收掉；键盘聚焦有全局
`:focus-visible` 焦点环；禁用保留方框但不抬升。**不位移**：新语言的高度由投影承担，
`transform` 不参与表达层级。

会话列表里的会话标题也是 `secondary`，而且 `w-full`：它和下面的「改名 / 删除」是同一排
控件，没有理由只有它没框；框与卡片同宽（铺满卡片内容区），长会话名在框内换行而不会撑破卡片。变体只有 primary / secondary / danger 三种，**每个都自带方框**；
原先那个无框的 `ghost` 变体在最后一个调用点消失后已删除（没有调用点的变体是不可验证的
死代码）。时间线动作行**常驻可见**：`opacity-0` + 悬停显形已撤销（「有这功能」不该先被
猜到），方框与高度档在静止时就在 DOM 里。

取值只来自 `ui/tokens.css`，所以换主题与整体缩放不需要动组件。回归用例：
`e2e/interaction.spec.ts`（断言**读 token 拼期望值**，不写字面量——换语言不必改测试），
「长什么样」由 `e2e/visual.spec.ts` 的视觉基线守。

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
