# web/ — Avid 前端

**现状：骨架（阶段 32 清空重建）。** 旧前端（页面 / 组件 / 样式 / 静态资源）已整体删除
（123 文件 / 10,433 行）。现在只有入口、占位壳，以及两份**契约种子**：

| 路径 | 为什么留下 |
|---|---|
| `src/api/types.ts` | REST 线格式的 TS 侧真源。`tests/test_wire_contract.py` 逐字段比对 pydantic DTO ↔ interface，删了它契约门禁失守 |
| `src/events/types.ts` | 事件名的前端单点（`EVENTS:BEGIN/END` 块）。`tests/test_event_contract.py` 要求它与 `runtime/events.py` 的 `EVENT_TYPES` 集合相等 |

两份种子都是纯类型与常量，不含任何页面代码；**改动它们等于改内核契约**，不是前端内部事务。

视觉风格与布局方案**尚未确认**：`tailwind.config.js` 里没有 token，也还没有 `tokens.css`。
新的设计落地前不要预先建 `ui/` `features/` `layouts/` `routes/` 目录——空壳会变成下一轮要拆的东西。

## 命令

```bash
pnpm install
pnpm dev            # Vite（代理 /api → 127.0.0.1:8765），需另起 `uv run avid web`
pnpm build          # tsc -b + vite build → dist/
pnpm run copy:dist  # dist → ../src/avid/web/static + .build.json 构建戳
                    # `avid web` 服务的是**这一份**、不是 web/dist。改了前端只 build 不 copy
                    # 的话，它会忠实地发一份旧页面；启动时会有一条 ⚠ 告警指出这件事。
pnpm typecheck      # tsc -b --noEmit
pnpm test           # vitest：目前只有 2 条骨架 smoke 用例（App.test.tsx）
```

**Node ≥ 22.22**：`.npmrc` 的 `engine-strict` 在 `pnpm install` 阶段就会按
`package.json` 的 `engines` 失败关闭——版本不够时装得下依赖，问题要等到 vitest 起
jsdom 才以一条 unhandled error 的形式暴露。

## 已删除、待按新设计重建的东西

| 东西 | 现状 |
|---|---|
| `ui/`（tokens + 原语 + 形态）、`features/`（8 个特性）、`layouts/`、`routes/` | 全删 |
| `e2e/`（Playwright 10 个 spec + 4 张视觉基线） | 全删；断言与选择器都指向旧页面 |
| `scripts/` 的 6 个门禁（分层 / token / 样式 / 对比度 / 体积 / 玻璃测量） | 全删；它们检查的目录结构与 token 名都不存在了 |
| `budget.json`（首屏 JS / 样式表 / 字体 / 纹理的冻结上限） | 删；记录的是旧 bundle 的体积，对新骨架没有约束力 |
| `playwright.config.ts` | 删 |

`scripts/copy-dist.mjs` 是唯一留下的脚本：它是产物交付链（`dist/` → `src/avid/web/static/`），
与页面设计无关，后端 `avid web` 依赖它。
