import { defineConfig } from '@playwright/test'

/**
 * 视觉与烟测的 Playwright 配置。
 *
 * 运行前先装浏览器（本机默认没装）：
 *   pnpm exec playwright install chromium
 * 再起内核（`uv run avid web`，监听 127.0.0.1:8765）后：
 *   AVID_E2E=1 pnpm test:e2e
 *
 * 截图约定（视觉回归的稳定性全在这两条上，写进每个用例的 beforeEach）：
 *   1. 冻结动效：`await page.addStyleTag({ content: '* { transition: none !important; animation: none !important }' })`
 *      —— 否则 --motion-press/hover/panel 会让同一状态截出不同帧。
 *   2. 等字体就绪：`await page.evaluate(() => document.fonts.ready)`
 *      —— 手写体（Avid Sketch）是 swap 加载，不等它就会截到回落的系统字体。
 * 首次运行没有基线时用 `--update-snapshots` 生成，之后基线入库、只在预期变更时更新。
 *
 * trace 关掉：截图 diff 已经能定位问题，trace 会把每个用例的体积放大一个量级。
 */
export default defineConfig({
  testDir: './e2e',
  baseURL: 'http://127.0.0.1:8765',
  // 串行：被测后端是**单进程**的（每会话一个活动 run 的注册表 + 同一份会话文件），
  // 多个 worker 并行造会话时会互相拖慢，表现为与本文件无关的偶发失败。
  workers: 1,
  use: {
    trace: 'off',
  },
})
