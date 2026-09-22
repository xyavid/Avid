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
* `AVID_E2E_PICK_FILE`：新增工作区的用例没法点真对话框（会挂住等人），所以服务端用它把
  选择器换成 `cat <该文件>`：**写路径 = 用户选了那个文件夹，写空 = 用户点了取消**。
  两个终端都要设它（服务端读它接管选择器，用例读它写路径）：
  `AVID_E2E_PICK_FILE=/tmp/avid-e2e-pick.txt`。不设时那条用例整体跳过。

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

**基线文件入库**（`e2e/visual.spec.ts-snapshots/`，约 320 KB）。**报警已验证**：把 `--r-card`
从 18px 故意改成 30px → 落点页基线 409 像素不同、测试红；还原即恢复。连跑 9 遍（含 20s
间隔）稳定。

### 容差与它的来历（实测，不要再猜）

**容差取 ≤3 像素且单通道差 ≤4**（等价于 `maxDiffPixels: 3`）。来历是阶段 23a 的一次对照：
把**同一份构建**在 5 个状态上各截两遍（1440×900、`deviceScaleFactor: 1`、
`reducedMotion: reduce`、每次新开 browser context），其中 1 个状态出现 3 个像素不同、
单通道最大差 4/255，位置固定为导航列第一个条目按钮的 1px 左边框。**写 0 会让同一份构建
自己报红**，门禁随即退化成「每次改动都点一下同意」。细节见
`docs/design/frontend-architecture.md` §8.9。

### 三类"必须先处理掉"的漂移（`visual.spec.ts` 各踩过一次）

1. **按表面截图，不截整页**。整页会把导航列带进来，而它的内容是"这套 e2e 跑到现在攒下的
   所有会话"——跑第二遍就不一样。`section.surface-main` / `section.surface-panel` 只取决于
   该用例自己造的数据。
2. **时钟在 `page.goto` 之前冻结**，且时刻**相对数据**取。审批卡那句"还有 30 秒"是
   `expiresAt - Date.now()`：导航之后再冻只冻住一个已经算好的值（秒数继续跳，第一版就是
   这样随机报红，差异只有一个字形）；冻在绝对常数上则差值随真实时钟漂移（第二版差的是
   "几小时前"的那个小时数）。审批那条因此冻在「还有整 1 小时」的时刻。
3. **随机 id 要遮罩**（`.id-tag` / `.id-tag-empty`）：会话与运行 id 是随机的，那是数据不是观感。

### 做「改动前后」对照时的两条硬要求

要判断「这次改动有没有改到视觉」，用**同一份数据、同一次会话**的前后对照，不要各跑一遍
用例（会话 id 与「几分钟前」会先自己造成差异）。阶段 23a 的做法（脚本在 `dev/tmp/`，
过程文件不入库）：两个服务进程只起一次（待决审批活在内存里，重启就没了），会话只造一次，
两次截图之间**只替换静态目录的内容**，于是两遍看到同一批 id、同一个创建时间。

对照时除了 PNG，还应当存下 `document.body.outerHTML` 并逐字节比对：它比像素更细，任何
class、属性、层级或文本变化都会露出来，而像素可能被同样的颜色掩盖。

## 截图稳定性

截图前必须在用例里补两步，否则同一状态会截出不同帧：

```ts
await page.addStyleTag({ content: '* { transition: none !important; animation: none !important }' })
await page.evaluate(() => document.fonts.ready)
```

前者冻掉 `--motion-*` 过渡，后者等字体就绪（阶段 23b 起字体只有系统栈，这一步仍保留：
它守着"将来若再引入自托管字体，截图不会截到回退字形"）。
`playwright.config.ts` 里 `trace: 'off'`：截图 diff 足够定位问题，trace 会让产物体积翻很多倍。

## 配置

- `testDir: './e2e'`
- `baseURL: 'http://127.0.0.1:8765'`（后端 `uv run avid web` 的地址）
