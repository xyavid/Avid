# e2e（Playwright）

浏览器不在依赖里，第一次要先装内核产物（Chromium 约 150 MB，只装一次）：

```bash
pnpm exec playwright install chromium
# Linux 上还缺系统库时：
pnpm exec playwright install-deps chromium
```

## 怎么跑

e2e 打的是**真实内核**，不是 Vite dev server：先起后端（默认 `127.0.0.1:8765`），再开一个终端跑测试。

```bash
# 终端 1（仓库根目录）
uv run avid web

# 终端 2
cd web
AVID_E2E=1 pnpm test:e2e
```

没有 `AVID_E2E=1` 时 `e2e/smoke.spec.ts` 会整体 `test.skip`——它属于交付前的自检，不属于 `pnpm test`。

### 用脚本模型跑（不需要真模型与密钥）

`dev/tmp/e2e_server.py` 起的是同一套 svc/web，只把模型与工具换成脚本。它服务
**`web/dist`**（当前 checkout 构建出来的前端），所以先构建：

```bash
pnpm -C web build                                  # 必须；缺 index.html 会直接报错退出
AVID_API_KEY=test AVID_MODEL=test-model AVID_PORT=8877 \
  uv run --extra web python dev/tmp/e2e_server.py  # 终端 1
cd web && AVID_E2E=1 AVID_BASE_URL=http://127.0.0.1:8877 pnpm test:e2e   # 终端 2
```

两个环境变量值得知道：

* `AVID_E2E_STREAM=1`：走**生产路径**（不注入 chat），delta 会真的经 SSE 到浏览器。
  `streaming.spec.ts` 需要它——脚本模型在非流式路径下不产生 delta，那条会失败（其余 35 项两种模式都过）。
* `AVID_PORT` / `AVID_BASE_URL`：本机 8765 常被别的进程占着，换端口即可，两边要一致。

只跑某个文件 / 带 UI 调试：

```bash
AVID_E2E=1 pnpm exec playwright test e2e/smoke.spec.ts
AVID_E2E=1 pnpm exec playwright test --ui
```

## 视觉回归基线

首次运行（或有意改 UI）时用 `--update-snapshots` 生成基线，基线入库后即为「当前认可的样子」：

```bash
AVID_E2E=1 pnpm exec playwright test --update-snapshots
```

之后正常运行会拿新截图与基线比对，diff 落在 `test-results/`。基线只在预期变更时更新，
否则这道门禁就退化成了「每次改动都点一下同意」。

## 截图稳定性

截图前必须在用例里补两步，否则同一状态会截出不同帧：

```ts
await page.addStyleTag({ content: '* { transition: none !important; animation: none !important }' })
await page.evaluate(() => document.fonts.ready)
```

前者冻掉 `--motion-*` 过渡，后者等自托管手写体（`Avid Sketch`，`font-display: swap`）加载完成。
`playwright.config.ts` 里 `trace: 'off'`：截图 diff 足够定位问题，trace 会让产物体积翻很多倍。

## 配置

- `testDir: './e2e'`
- `baseURL: 'http://127.0.0.1:8765'`（后端 `uv run avid web` 的地址）
