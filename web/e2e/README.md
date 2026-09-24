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

**这一步的前置服务不在仓库里**，先说清楚：`dev/` 是过程目录、整目录不入库，所以
「起一个脚本模型内核」这件事必须自备。它要有四个性质（照这四条写一个几十行的装配即可，
真实装配的形状见 `dev/tmp/e2e_server.py`，但那个文件只存在于开发机上）：

1. **同一个 `create_app` + `Services`**，只把模型与工具换成脚本——不是另写一个假服务，
   否则用例测的就不是生产装配；
2. 注入 `chat`（`AVID_E2E_STREAM=1` 时改注入 `stream_completion`，于是 svc 走生产路径、
   delta 真的经 SSE 出去）；
3. **服务 `web/dist`** 而不是 `src/avid/web/static`——后者是 `copy:dist` 的产物，容易停在
   上一版（改了前端却测出「选择器不存在」）；
4. `AVID_HOME` 与工作区根都指到临时目录——否则跑一次 e2e 就把仓库目录登记进用户真实的
   `~/.avid/workspaces.json`，并把测试垃圾会话写进真实会话列表；
5. **它的第一次工具调用要落在 REVIEW 上**（阶段 26 之后新增的性质）。三档预设里 `manual`
   与 `auto` 都是"沙箱内免问"，所以 `bash echo hi` 这类工作区内命令**不会**弹审批——
   而 `conversation.spec.ts`（提交 → 审批 → 完成）、`streaming.spec.ts` 与视觉基线里的
   "审批待决"都要等一次真实审批。用一条**危险命令**即可（提权、递归删除、
   `curl | sh` 等，三种模式一律问一次），例如第一条工具调用发
   `bash {"command": "sudo true"}`——工具结果是桩，命令不会真的执行。本地那份服务里，
   这一条就是 `chat()` 第一轮返回的 tool_call。

   为什么写进前置条件而不是把用例改成"审批可来可不来"：审批队列是**被守的对象**，
   让它可来可不来等于把这条路径的回归悄悄关掉。（`branches.spec.ts` 是唯一的例外：
   它的分叉点只能落在模型侧条目上，新链因此必然带着那一轮的 tool 消息，脚本模型不再
   要工具、也就没有审批可等——那条用例改为断言请求体里的 `branch`。）

```bash
pnpm -C web build                                  # 必须；缺 index.html 时你那个服务应直接报错退出
AVID_API_KEY=test AVID_MODEL=test-model AVID_PORT=8877 \
  uv run --extra web python <你的脚本模型服务>.py   # 终端 1
cd web && AVID_E2E=1 AVID_BASE_URL=http://127.0.0.1:8877 pnpm test:e2e   # 终端 2
```

两个环境变量值得知道：

* `AVID_E2E_STREAM=1`：走**生产路径**（不注入 chat），delta 会真的经 SSE 到浏览器。
  `streaming.spec.ts` 需要它——脚本模型在非流式路径下不产生 delta，那条会失败
  （其余用例两种模式都过）。
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

## 为什么不进 CI（阶段 23c 的决策）

**结论：不接。** 两条理由都是具体的，不是"以后再说"：

1. **CI 里没有模型与密钥，而脚本模型服务不在仓库里**（上面那四条前置）。所以"把 e2e 接进
   CI"的第一步其实是另一件事：**把那个服务变成受审的仓库文件**（它要读 `~/` 与工作区、
   要写临时目录、要能注入模型——审查面不小），再接一个与 `web` job 并列的 job。
2. **视觉基线是环境绑定的**。那 5 张基线是在本机这套字体栈上截的（Linux/WSL + 系统 CJK
   字体），CI 的镜像字体不同 ⇒ 基线必红。Playwright 的标准解法是把 job 放进它的官方容器
   镜像里把环境钉死，而那会让"接 e2e"变成"接 e2e + 换运行环境 + 重截一次基线"。

**重新考虑的信号**：① 有人愿意把那 42 条功能用例的服务器移进仓库（那是一件独立的事，
值得单开一步）；② 出现"改了前端 CI 全绿、本地才发现 e2e 红"的实际事故。在那之前，
e2e 与视觉基线都是**交付前的手动自检**——它的价值已经兑现过（23b 的两处真实回退就是它
抓出来的：`:active` 被 `:hover` 盖住、背景插画退化路径撞 CSP），只是没进自动门禁。

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
