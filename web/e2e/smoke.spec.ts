/**
 * 基座自检：证明"真实浏览器 + `/api/**` 打桩"这条链路是通的。
 *
 * 它**不验业务**——业务在各自的 spec 里。这里只回答一个问题：
 * 打桩装上了吗、页面真的用上了桩数据吗、控制台干净吗。
 * 基座不自检的话，后面所有 spec 的失败都会先被怀疑成"是不是打桩没生效"。
 */

import { expect, test } from './lib/fixtures'
import { defaultSession, defaultWorkspace } from './lib/api'

test.describe('E2E 基座', () => {
  test('页面用桩数据渲染出会话名与工作区名', async ({ page, stubApi }) => {
    await stubApi({
      sessions: [defaultSession('s-1', 'E2E 的会话')],
      workspaces: [defaultWorkspace('ws-1', '/home/fishy/Avid', 'E2E 工作区')],
    })

    await page.goto('/', { waitUntil: 'domcontentloaded' })

    // 会话名出现在对话页头部的 h1（唯一的无歧义锚点：左栏也有会话名。
    // 但这里恰好只有一个会话，所以断言落到 h1 上更稳）。
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('E2E 的会话')
    // 工作区名来自桩数据，出现在左栏分组里。
    await expect(page.getByText('E2E 工作区')).toBeVisible()
  })

  test('桩里记录到界面实际发出的请求', async ({ page, stubApi }) => {
    const stub = await stubApi({})
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    // 首屏的全局请求：能力面、会话列表、工作区列表。
    expect(stub.calls('GET', '/meta').length).toBeGreaterThan(0)
    expect(stub.calls('GET', '/sessions').length).toBeGreaterThan(0)
    expect(stub.calls('GET', '/workspaces').length).toBeGreaterThan(0)
    // 默认回落到第一个会话后，该会话的条目与分支列表也要拉。
    expect(stub.calls('GET', '/sessions/s-1/entries').length).toBeGreaterThan(0)
    expect(stub.calls('GET', '/sessions/s-1/branches').length).toBeGreaterThan(0)

    /*
     * 为什么用"至少一次"而不是"恰好一次"：E2E 跑在 Vite **开发**服务器上，
     * 而 `main.tsx` 包着 `StrictMode`——开发态 React 会**故意双跑 effect**，
     * 于是首屏请求各发两次。这是 StrictMode 的设计意图（暴露副作用没清理的情况），
     * 不是缺陷：实测同一份代码走 `vite preview`（生产产物）时每个端点只请求一次。
     *
     * 真正值得在这里钉住的是**没有请求不该请求的端点**——它比计数更能抓回归。
     */
    const allowed = [
      '/meta',
      '/sessions',
      '/workspaces',
      '/sessions/s-1/entries',
      '/sessions/s-1/branches',
    ]
    const requested = new Set(stub.log.map((entry) => entry.path))
    const unexpected = [...requested].filter((path) => !allowed.includes(path))
    expect(unexpected, `首屏请求了未预期的端点：${unexpected.join(', ')}`).toEqual([])
  })

  test('未打桩的端点会明确失败，而不是静默成功', async ({ page, stubApi }) => {
    await stubApi({})
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    const unknown = await page.evaluate(async () => {
      const response = await fetch('/api/definitely-not-stubbed')
      return { status: response.status, body: await response.text() }
    })
    expect(unknown.status).toBe(404)
    expect(unknown.body).toContain('E2E 未打桩')
  })
})
