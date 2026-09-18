import { expect, test } from '@playwright/test'

/**
 * 路由最小实验：四条工作面各自渲染出自己的内容，未知路径回落会话页，
 * 并且每次切换都不产生页面错误（React 崩溃会在 pageerror 里出现）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'

const PAGES = [
  { path: '/sessions', marker: '会话' },
  { path: '/tasks', marker: '任务板' },
  { path: '/skills', marker: '技能目录' },
  { path: '/settings', marker: '设置' },
]

for (const page of PAGES) {
  test(`路由 ${page.path} 渲染工作面且无页面错误`, async ({ page: browserPage }) => {
    const errors: string[] = []
    browserPage.on('pageerror', (error) => errors.push(error.message))
    browserPage.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text())
    })

    await browserPage.goto(`${BASE}${page.path}`)
    await expect(browserPage.getByText(page.marker).first()).toBeVisible()
    expect(errors).toEqual([])
  })
}

test('根路径与未知路径都落到会话页', async ({ page }) => {
  await page.goto(`${BASE}/`)
  await expect(page).toHaveURL(/\/sessions$/)

  await page.goto(`${BASE}/this-route-does-not-exist`)
  await expect(page).toHaveURL(/\/sessions$/)
})

test('检查器的三个视图就地切换，不进 URL 历史', async ({ page }) => {
  const listed = await page.request.get(`${BASE}/api/sessions`)
  const sessions: { id: string; message_count: number }[] = (await listed.json()).sessions
  const target = sessions.find((item) => item.message_count > 0)
  test.skip(!target, '需要一个有条目的会话')

  await page.goto(`${BASE}/sessions/${target?.id}`)
  const url = page.url()
  await page.getByRole('button', { name: '查看' }).first().click()
  await page.getByRole('tab', { name: 'diff' }).click()
  await page.getByRole('tab', { name: '原始 JSON' }).click()
  expect(page.url()).toBe(url)
})
