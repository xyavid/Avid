import { expect, test } from '@playwright/test'

// 没有 AVID_E2E=1（或内核没起）时整文件跳过：e2e 不该在 `pnpm test` 里误跑。
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

// 用绝对地址而不是 `page.goto('/')`：相对路径依赖 config 的 baseURL 解析，
// 出问题时是一个难以定位的 Protocol error。
const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'

test('首屏可交互：h1 可见、焦点不在 body、没有外部字体请求', async ({ page }) => {
  const externalFontRequests: string[] = []

  // 用 page.route 统计而不是事后看 network 日志：请求一旦发出就算，避免竞态漏数。
  await page.route('**/*', async (route) => {
    const url = route.request().url()
    if (url.includes('fonts.googleapis.com') || url.includes('fonts.gstatic.com')) {
      externalFontRequests.push(url)
    }
    await route.continue()
  })

  await page.goto(`${BASE}/sessions`)

  await expect(page.locator('h1').first()).toBeVisible()

  // 首屏必须已经有可交互的东西（输入框等）拿到焦点，而不是停在 body 上。
  const focused = await page.evaluate(() => document.activeElement !== document.body)
  expect(focused).toBe(true)

  // 自托管字体：任何一次 Google Fonts 请求都是回归。
  expect(externalFontRequests).toEqual([])

  // 如果这个用例要截图做视觉回归，截图前先冻结动效并等字体就绪：
  //   await page.addStyleTag({
  //     content: '* { transition: none !important; animation: none !important }',
  //   })
  //   await page.evaluate(() => document.fonts.ready)
})
