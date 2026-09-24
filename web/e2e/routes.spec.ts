import { expect, test } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

/**
 * 路由最小实验：四条工作面各自渲染出自己的内容，未知路径回落会话页，
 * 并且每次切换都不产生页面错误（React 崩溃会在 pageerror 里出现）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'

const PAGES = [
  { path: '/sessions', marker: '会话' },
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

test('会话落点页给的是指路文案，不是「还没有会话」', async ({ page }) => {
  // 导航列里可能正列着一堆会话（「会话」不是导航项之后，这个页面只作为根路径与未知
  // 路径的落点），所以「还没有会话」说出口就是错的——那句是空工作区文件夹的文案。
  await page.goto(`${BASE}/sessions`)
  const landing = page.locator('section.surface-main')
  await expect(landing.getByText('从左侧选一个会话开始')).toBeVisible()
  await expect(landing.getByText('还没有会话')).toHaveCount(0)
})

/**
 * 找一个**短且有工具调用**的会话：检查器现在由工具卡的「查看」打开。
 *
 * 「短」是必要条件：时间线默认只渲染尾部一组，长会话里那张 CALL 卡不在 DOM 里
 * （以前任取一个有条目的会话就行——那靠的是每个条目都有的按钮）。
 */
async function sessionWithToolCall(request: APIRequestContext): Promise<string | null> {
  const listed = await request.get(`${BASE}/api/sessions`)
  const sessions = (await listed.json()).sessions as { id: string }[]
  for (const session of sessions.slice(0, 20)) {
    const page = await request.get(
      `${BASE}/api/sessions/${session.id}/entries?order=asc&limit=50`,
    )
    const entries = (await page.json()).entries as { message?: { role?: string } }[]
    if (entries.length > 8) continue
    if (entries.some((entry) => entry.message?.role === 'tool')) return session.id
  }
  return null
}

/**
 * 打开检查器：它现在由**工具卡**的「查看」打开（条目级的「查看原始 JSON」已删）。
 * 工具卡默认折叠，所以先点开那张 `CALL` 卡再点它的「查看」。
 */
async function openInspector(page: Page): Promise<void> {
  await page
    .getByRole('button')
    .filter({ hasText: 'CALL' })
    .first()
    .click()
  const card = page.locator('section.surface-card').filter({ hasText: 'CALL' }).first()
  await card.getByRole('button', { name: '查看' }).click()
}

test('检查器的三个视图就地切换，不进 URL 历史', async ({ page, request }) => {
  const target = await sessionWithToolCall(request)
  test.skip(!target, '需要一个含工具调用的会话')

  await page.goto(`${BASE}/sessions/${target}`)
  const url = page.url()
  await openInspector(page)
  await page.getByRole('tab', { name: 'diff' }).click()
  await page.getByRole('tab', { name: '原始 JSON' }).click()
  expect(page.url()).toBe(url)
})
