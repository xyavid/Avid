# web/ — Avid 前端

**现状：对话主页面已可用。** 阶段 32 把旧前端（123 文件 / 10,433 行）整体删除，
本轮按「纸本世界」视觉语言重建，范围是**对话主页面**：会话导航列 + 对话主区
（时间线 / 审批 / 输入区）+ 右栏信息面板与检查器，另含工作区的添加与管理。

## 命令

```bash
pnpm install
pnpm dev            # Vite（代理 /api → 127.0.0.1:8765），需另起 `uv run avid web`
pnpm build          # tsc -b + vite build → dist/
pnpm run copy:dist  # dist → ../src/avid/web/static + .build.json 构建戳
                    # `avid web` 服务的是**这一份**、不是 web/dist。改了前端只 build 不 copy
                    # 的话，它会忠实地发一份旧页面；启动时会有一条 ⚠ 告警指出这件事。
pnpm typecheck      # 两条配置都跑：浏览器侧（tsc -b）+ e2e 侧（tsconfig.e2e.json）
pnpm test           # vitest 单测（约 340 条）
pnpm e2e            # Playwright：真 chromium + 打桩后端，约 18 条
pnpm e2e:ui         # 同上，带 UI 观察器
```

**Node ≥ 22.22**：`.npmrc` 的 `engine-strict` 在 `pnpm install` 阶段就会按
`package.json` 的 `engines` 失败关闭。

## 分层与依赖方向

```
styles/tokens.css        设计 token 唯一来源（颜色 / 间距 / 字号 / 圆角 / 线宽 / 动效）
      ↓
ui/                      原语（Button / Badge / Card / Dialog / Tooltip）+ 线性图标 + markdown
      ↓
features/                sessions · conversation · composer · inspector · workspace（各自带 lib/ 与用例）
      ↓
layouts/                 AppShell（三栏 + 顶部条）· ConversationPage · RightRail · InspectorSlot
      ↓
App.tsx                  装配；api/ + events/ 给数据，state/ 管状态
```

只向下依赖。`api/client.ts` 是**唯一网络出口**（这层之外不得出现 `fetch`）。

## 契约种子（改动等于改内核契约）

| 路径 | 守它的门禁 |
|---|---|
| `src/api/types.ts` | `tests/test_wire_contract.py` 逐字段比对 pydantic DTO ↔ interface |
| `src/events/types.ts` | `tests/test_event_contract.py` 要求它与 `runtime/events.py` 的 `EVENT_TYPES` 集合相等 |

两份都是纯类型与常量。**不要为了顺手而改它们**——线格式变了要同步改内核侧的门禁。

## E2E 怎么跑、以及它不证明什么

`web/e2e/` 的用例跑在真 chromium 上，但**不起后端**：`/api/**` 由 `e2e/lib/api.ts`
在浏览器层打桩。所以它不依赖 `uv`、不依赖模型密钥、不受本机工作区状态影响——
任何人任何机器 `pnpm e2e` 都是同一结论。

- **它证明**：接线与呈现（SSE 接上了、事件画到屏幕了、按钮状态跟着事件变、布局没被撑坏、
  token 真的落到元素上、错误路径给了对的文案）。
- **它不证明**：线格式对不对（那是后端契约测试的事）、真实联调能不能跑通。
  真实联调：`uv run --env-file .env avid web --port 8765` + `pnpm dev`。

两条约定：
1. 每条用例**自动断言控制台零错误**（`e2e/lib/fixtures.ts`）。React 的 key 冲突、
   无效嵌套、未捕获 Promise 都只出现在控制台里而页面看着能跑。
   要放行就在用例里显式 `allowConsoleError('…')`，别放宽全局判据。
2. **未打桩的端点返回 404 且带路径**，不静默成功——否则"界面调了不该调的端点"永远查不出来。

`test-results/` 与 `playwright-report/` 不入库（失败时的截图与 trace 留本地按需翻）。

## 视觉纪律（改动前先读）

token 唯一来源是 `src/styles/tokens.css`，`tailwind.config.js` 只做**名字绑定**，
组件里不得出现裸色值、`shadow-[…]` 任意值、内联 `borderRadius` / `fontFamily`。
字重上限 500，圆角上限 4px（只有头像与状态点用 `rounded-full`），图标全是线性 SVG（无 emoji）。

三条**踩过的坑**，写在这里以免重复踩：
- **线宽与线色是两个轴**：`border-hair` 同时给 0.5px 与线色；方向变体自带线色。
  不要写 `border-t-hair border-hair`——那会画出**四边整框**（详见 `styles/tokens.css` 与 `ui/cx.ts` 的注释）。
- **Tailwind 同轴类按类名字母序输出**，所以 `border-hair` 会静默覆盖字典序在它之前的颜色类。
  要覆颜色请用字典序在 `hair` 之后的具名 token（`border-state-danger` / `border-warn`）。
- **文字强调色用 `text-accent-ink`，不是 `text-accent`**：亮青绿 `#5BA88C` 作正文只有 2.45:1，
  深青绿 `#3B745D` 才过 AA。`bg-accent` 只用于填充（配 `text-accent-deep`）。

## 已知取舍

- **正文富文本用 `react-markdown` + `remark-gfm`**，未开 `rehype-raw`（不渲染原始 HTML——
  正文来自模型，渲染原始 HTML 等于把 XSS 面重新打开）。代价是首屏 JS 从约 209 kB 涨到约 381 kB
  （gzip 68 → 121 kB）。若将来在意体积，可换更小的 micromark 直出方案，但会丢掉组件映射的便利。
- **不做移动端布局**，只保证桌面端；窄屏只做降级（导航列变抽屉、检查器变浮层）。
- **检查器 diff 是行级自实现 LCS**，不是完整 diff 算法；带行号与增删底色的展示够用，
  但不做 hunk 折叠与词级高亮。
- `scripts/` 的六个门禁（分层 / token / 样式 / 对比度 / 体积 / 玻璃测量）与 `budget.json`
  仍**未重建**。目前靠 code review + E2E 的计算样式断言守视觉纪律，比原脚本弱。
