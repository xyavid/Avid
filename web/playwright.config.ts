/**
 * Playwright 配置。
 *
 * 阶段 32 删掉了旧前端的 Playwright 配置与 10 个 spec（断言与选择器都指向旧页面），
 * 本次按新结构重建。两条设计决定：
 *
 * 1. **只起 Vite，不起后端**。`/api/**` 由 `e2e/lib/api.ts` 在浏览器层打桩。
 *    这样 E2E 不依赖 `uv`、不依赖模型密钥、不受本机工作区状态影响——
 *    任何人在任何机器上 `pnpm e2e` 都能得到同一结论。真实联调另有其路径（见 web/README.md）。
 *
 * 2. **只用 chromium**。本次范围是桌面端界面；跨浏览器矩阵要有明确理由才加
 *    （体感差异、私有 API、字体回退），现在没有。
 *
 * `reuseExistingServer: !CI`：本地重复跑时复用已起的 Vite，省掉每次冷启动；
 * CI 上必须每次新起，否则"跑的是上一次的产物"这类假绿无法排除。
 */

import { defineConfig, devices } from '@playwright/test'

/** 独立端口，避开 `pnpm dev` 默认的 5173——两个进程同时开着时不至于互抢。 */
export const E2E_PORT = 5180

export default defineConfig({
  testDir: './e2e',
  // 只收 spec；lib/ 是基座，不是用例。
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  timeout: 20_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: `http://127.0.0.1:${E2E_PORT}`,
    // 失败时留证据：截图看"长什么样"，trace 看"怎么走到这一步的"。两者都要，
    // 因为"报错信息说找不到元素"经常既不告诉你它长什么样，也不告诉你它为什么不在。
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  },

  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  webServer: {
    command: `pnpm exec vite --port ${E2E_PORT} --strictPort`,
    url: `http://127.0.0.1:${E2E_PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
