import { expect, test } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

/**
 * 布局回归：输入条必须始终落在可视区域内，消息只在会话区域里独立滚动，
 * 页面本身不产生纵向溢出。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可，见 docs/guide/web-ui.md）。
 * 用真实接口造一个「条目多且单条长」的会话：每轮 run 追加 4 条条目，
 * 用户轮的 prompt 自带长文本，因此不依赖任何测试专用写入口。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const COMPOSER_LABEL = '输入指令，Enter 发送，Shift+Enter 换行'
const VIEWPORTS = [
  { width: 1440, height: 560 },
  { width: 1280, height: 720 },
  { width: 1280, height: 900 },
  { width: 1100, height: 700 },
  { width: 900, height: 600 },
]

/**
 * 起一次运行并等到注册表**真的释放**会话。
 *
 * 只等 run 状态不够：注册表在发出终态事件之后才释放会话占位，紧接着的 POST 会
 * 撞上 409 run_busy。夹具按 409 重试，并等 `active_run_id` 清空。
 */
async function runToIdle(
  request: APIRequestContext,
  sessionId: string,
  prompt: string,
): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const response = await request.post(`${BASE}/api/sessions/${sessionId}/runs`, {
      data: { prompt, auto_approve: true },
    })
    if (response.status() === 409) {
      await new Promise((resolve) => setTimeout(resolve, 50))
      continue
    }
    expect(response.status(), `起运行失败：${await response.text()}`).toBe(201)
    const runId = (await response.json()).run_id as string
    for (let poll = 0; poll < 100; poll += 1) {
      const run = await (await request.get(`${BASE}/api/runs/${runId}`)).json()
      const detail = await (await request.get(`${BASE}/api/sessions/${sessionId}`)).json()
      const settled = run.status !== 'running' && run.status !== 'awaiting_approval'
      if (settled && !detail.active_run_id) return
      await new Promise((resolve) => setTimeout(resolve, 50))
    }
    return
  }
  throw new Error('反复遇到 run_busy：注册表没有释放会话')
}

async function openSession(page: Page, sessionId: string): Promise<void> {
  await page.goto(`${BASE}/sessions/${sessionId}`)
  await expect(page.getByLabel(COMPOSER_LABEL)).toBeVisible()
  // 等条目真的渲染出来：否则测到的是空会话，输入条可见没有意义。
  await expect
    .poll(async () => (await page.getByRole('log').innerText()).length, { timeout: 10_000 })
    .toBeGreaterThan(50)
}

async function longSession(request: APIRequestContext): Promise<string> {
  const created = await request.post(`${BASE}/api/sessions`, { data: { name: '布局验证' } })
  const sessionId = (await created.json()).id as string
  for (let round = 0; round < 12; round += 1) {
    await runToIdle(
      request,
      sessionId,
      `第 ${round} 轮：` + '这是一段用来撑高会话区域的长内容。'.repeat(12),
    )
  }
  return sessionId
}

interface Metrics {
  scrollHeight: number
  bodyScrollHeight: number
  innerHeight: number
  composerTop: number
  composerBottom: number
  logClientHeight: number
  logScrollHeight: number
  pageOverflow: boolean
}

async function measure(page: Page): Promise<Metrics> {
  return page.evaluate(() => {
    const composer = document.querySelector('textarea')?.closest('div') as HTMLElement
    const log = document.querySelector('[role="log"]') as HTMLElement
    const box = composer.getBoundingClientRect()
    return {
      scrollHeight: document.documentElement.scrollHeight,
      bodyScrollHeight: document.body.scrollHeight,
      innerHeight: window.innerHeight,
      composerTop: box.top,
      composerBottom: box.bottom,
      logClientHeight: log.clientHeight,
      logScrollHeight: log.scrollHeight,
      pageOverflow: document.documentElement.scrollHeight > window.innerHeight + 1,
    }
  })
}

function expectComposerInsideViewport(metrics: Metrics, label: string): void {
  expect(metrics.pageOverflow, `${label}：页面不应纵向溢出`).toBe(false)
  expect(metrics.composerTop, `${label}：输入条顶部不应被推到视口上方`).toBeGreaterThanOrEqual(0)
  expect(
    metrics.composerBottom,
    `${label}：输入条底部必须落在视口内`,
  ).toBeLessThanOrEqual(metrics.innerHeight + 1)
  expect(metrics.bodyScrollHeight, `${label}：body 也不应溢出`).toBeLessThanOrEqual(
    metrics.innerHeight + 1,
  )
}

test('输入条在常见视口下都完整可见，页面无纵向溢出', async ({ page, request }) => {
  const sessionId = await longSession(request)

  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport)
    await openSession(page, sessionId)

    const metrics = await measure(page)
    const label = `${viewport.width}×${viewport.height}`
    expectComposerInsideViewport(metrics, label)
    // 内容确实比可视区高，才说明这条用例有意义（否则输入条可见是自然结果）
    expect(metrics.logScrollHeight, `${label}：会话区域内容应当超出可视高度`).toBeGreaterThan(
      metrics.logClientHeight,
    )
  }
})

test('窗口高度变化后输入条仍在视口内', async ({ page, request }) => {
  const sessionId = await longSession(request)
  await page.setViewportSize({ width: 1280, height: 900 })
  await openSession(page, sessionId)

  for (const height of [800, 640, 520, 720]) {
    await page.setViewportSize({ width: 1280, height })
    await page.waitForTimeout(150)
    expectComposerInsideViewport(await measure(page), `高度 ${height}`)
  }
})

test('会话区域独立滚动：输入条不动，消息区自己滚', async ({ page, request }) => {
  const sessionId = await longSession(request)
  await page.setViewportSize({ width: 1280, height: 720 })
  await openSession(page, sessionId)

  const before = await measure(page)
  const scrolled = await page.evaluate(() => {
    const log = document.querySelector('[role="log"]') as HTMLElement
    log.scrollTop = 0
    return log.scrollTop
  })
  expect(scrolled, '会话区域应当可以独立滚动到顶部').toBe(0)

  const after = await measure(page)
  expectComposerInsideViewport(after, '滚动后')
  expect(Math.abs(after.composerBottom - before.composerBottom)).toBeLessThanOrEqual(1)
  expect(await page.evaluate(() => window.scrollY), '页面自身不应被滚动').toBe(0)
})

test('其他工作面在视口内滚动，不产生页面溢出', async ({ page }) => {
  // 高度链改成视口高度后，非会话页必须有**自己的**滚动容器，否则内容会被裁掉。
  for (const path of ['/tasks', '/skills', '/settings']) {
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.goto(`${BASE}${path}`)
    await expect(page.getByRole('main')).toBeVisible()
    await page.waitForTimeout(200)
    const metrics = await page.evaluate(() => {
      const container = document.querySelector('#main > div') as HTMLElement
      return {
        pageOverflow: document.documentElement.scrollHeight > window.innerHeight + 1,
        clientHeight: container.clientHeight,
        scrollHeight: container.scrollHeight,
        overflowY: getComputedStyle(container).overflowY,
      }
    })
    expect(metrics.pageOverflow, `${path}：页面不应纵向溢出`).toBe(false)
    expect(metrics.overflowY, `${path}：工作面自己应当是滚动容器`).toBe('auto')
    // 内容高过可视区时必须可滚（否则等于被裁掉）
    if (metrics.scrollHeight > metrics.clientHeight) {
      const scrolled = await page.evaluate(() => {
        const container = document.querySelector('#main > div') as HTMLElement
        container.scrollTop = 200
        return container.scrollTop
      })
      expect(scrolled, `${path}：内容超高时应该能滚动`).toBeGreaterThan(0)
    }
  }
})

test('发送与加载更早之后输入条依然可见', async ({ page, request }) => {
  const sessionId = await longSession(request)
  await page.setViewportSize({ width: 1280, height: 720 })
  await openSession(page, sessionId)
  const composer = page.getByLabel(COMPOSER_LABEL)

  // 加载更早：窗口化只加载一组，点一次后再检查布局
  const earlier = page.getByRole('button', { name: '加载更早' })
  if ((await earlier.count()) > 0) {
    await earlier.first().click()
    await page.waitForTimeout(200)
    expectComposerInsideViewport(await measure(page), '加载更早之后')
  }

  // 发送：新条目进入时间线后输入条仍在视口内，且消息区继续独立滚动。
  // 注意不能等「审批卡」：长会话的历史里已经有 tool 消息，脚本模型会直接收尾。
  const prompt = '布局验证：追加一条长消息 ' + '内容 '.repeat(60)
  await composer.fill(prompt)
  await composer.press('Enter')
  await expect(page.getByRole('log').getByText(prompt.slice(0, 12)).first()).toBeVisible({
    timeout: 15_000,
  })
  expectComposerInsideViewport(await measure(page), '提交之后')
})
