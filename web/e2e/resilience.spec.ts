/**
 * 后端不可达时的界面表现。
 *
 * 这条用例是**一次真实报障的复现**：`pnpm dev` 起在前端、后端没起在代理指向的端口，
 * 于是 Vite 代理给每个 `/api` 回一条 `500` + 空正文，界面显示
 * `HTTP 500（unexpected_response）`——一个把人引向"服务端内部错误"的消息，
 * 而真实原因是"后端不在这儿"，两者该做的事完全不同。
 *
 * 所以这里钉的不是"会不会报错"，而是**报的错能不能指到真正的原因**。
 * 也顺带钉住它不依赖"有会话"：失败发生在首屏三个请求上，页面必须仍然可用
 * （给出提示，而不是白屏或假装一切正常）。
 */

import { expect, test } from './lib/fixtures'

test.describe('后端不可达', () => {
  test('代理 500 + 空正文时给出可执行的提示，而不是 HTTP 500', async ({ page }) => {
    /*
     * 手工搭这条链路，而不是用 `stubApi`：要的就是"桩也不在了"的效果——
     * Vite 代理在目标端口无人监听时回的正是 500 + text/plain + 空体。
     */
    await page.route(/^https?:\/\/[^/]+\/api\//, (route) =>
      route.fulfill({ status: 500, contentType: 'text/plain', body: '' }),
    )

    await page.goto('/', { waitUntil: 'domcontentloaded' })

    const banner = page.getByRole('status').first()
    await expect(banner).toBeVisible()
    // 必须指出"连不上本地服务"并给出端口线索——这是这条提示存在的唯一理由。
    await expect(banner).toContainText('连不上本地服务')
    await expect(banner).toContainText('8765')
    // 旧实现的文案不该再出现（它就是被这条用例替代掉的那个错误信息）。
    await expect(page.getByText(/unexpected_response/)).toHaveCount(0)

    /*
     * 失败不等于白屏：页面骨架与空态仍在。
     * 按区域收窄——左栏头部与主区落地页**各有一个**「新建会话」按钮
     * （前者常驻可用，后者在缺工作区时禁用），全局查询会因命中两个而失败。
     */
    const main = page.locator('#avid-main')
    await expect(main.getByRole('button', { name: '新建会话' })).toBeVisible()
    await expect(page.locator('nav[aria-label="会话"]')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight + 1)).toBe(false)
  })

  test('后端活着但某个端点返回 500 时，不误判成"后端没起"', async ({ page }) => {
    /*
     * 同一个代理、不同的处境：`/api/health` 是通的，坏的是某一个端点。
     * 此时**不能**再说"连不上本地服务"——那会把排查引到错误的层。
     */
    await page.route(/^https?:\/\/[^/]+\/api\//, (route) => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/health') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'ok', api_version: 1, uptime_ms: 1 }),
        })
      }
      return route.fulfill({ status: 500, contentType: 'text/plain', body: '' })
    })

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('status').first()).toBeVisible()

    // 提示里不该出现"连不上本地服务"这个结论。
    const text = await page.getByRole('status').allTextContents()
    expect(text.join(' ')).not.toContain('连不上本地服务')
  })
})
