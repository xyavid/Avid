import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

/**
 * F3 的端到端：delta 从内核经 SSE 真的流到浏览器，最后由 durable 消息收敛成**一条**。
 *
 * 需要 `AVID_E2E=1`，且内核以 `AVID_E2E_STREAM=1` 起（见 docs/guide/web-ui.md）——
 * 那个模式不注入 chat，于是 svc 走生产路径，delta 才会真的产生。
 *
 * 「收到过 delta」这件事必须被**观测**而不是推断：常规手段看不到 SSE 帧的内容，
 * 所以这里在页面里包一层 `window.fetch`，tee 出事件流并数 `assistant_delta` 帧。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const COMPOSER_LABEL = '输入指令，Enter 发送，Shift+Enter 换行'
const REPLY = '做完了'

async function deltaFrames(page: Page): Promise<number> {
  return page.evaluate(
    () => (window as unknown as { __deltaFrames?: number }).__deltaFrames ?? 0,
  )
}

test('delta 真的到达浏览器，且最终收敛成一条消息（不重复、不残留）', async ({
  page,
  request,
}) => {
  const meta = await (await request.get(`${BASE}/api/meta`)).json()
  test.skip(meta.features?.deltas !== 1, '内核没有声明 deltas 能力（需要 AVID_E2E_STREAM=1）')

  await page.addInitScript(() => {
    const state = window as unknown as { __deltaFrames: number }
    state.__deltaFrames = 0
    const original = window.fetch.bind(window)

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await original(input, init)
      const url =
        typeof input === 'string'
          ? input
          : input instanceof Request
            ? input.url
            : String(input)
      if (!url.includes('/events') || !response.body) return response

      const [app, probe] = response.body.tee()
      void (async () => {
        const reader = probe.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const chunk = await reader.read()
          if (chunk.done) break
          buffer += decoder.decode(chunk.value, { stream: true })
          const frames = buffer.split('\n\n')
          buffer = frames.pop() ?? ''
          for (const frame of frames) {
            if (frame.includes('event: assistant_delta')) state.__deltaFrames += 1
          }
        }
      })()

      // 交回给应用的是 tee 出来的另一支，内容与状态码、头都保持一致。
      return new Response(app, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      })
    }
  })

  const created = await request.post(`${BASE}/api/sessions`, {
    data: { name: `流式验证-${Date.now()}` },
  })
  const sessionId = (await created.json()).id as string

  await page.goto(`${BASE}/sessions/${sessionId}`)
  const composer = page.getByLabel(COMPOSER_LABEL)
  await expect(composer).toBeVisible()

  await composer.fill('流式验证：请先跑一次工具再给结论')
  await composer.press('Enter')

  // 脚本模型先要一次 bash，默认需要审批；批准后才走到有正文的那一轮（delta 也在那时产生）。
  const allow = page.getByRole('button', { name: '允许一次' })
  await expect(allow).toBeVisible({ timeout: 15_000 })
  await allow.click()

  const log = page.getByRole('log')
  await expect(log.getByText(REPLY, { exact: true })).toBeVisible({ timeout: 20_000 })

  // 观测到 delta 帧：说明客户端带了 ?deltas=1，且内核确实在推。
  await expect.poll(() => deltaFrames(page), { timeout: 10_000 }).toBeGreaterThan(0)

  // 收敛：乐观条目必须被 durable 消息取代，最终文本只出现一次（I12）。
  const occurrences = await log.getByText(REPLY, { exact: true }).count()
  expect(occurrences, '最终文本只应出现一次（乐观条目已被 durable 消息替换）').toBe(1)
})
