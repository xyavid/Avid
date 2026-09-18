import { expect, test } from '@playwright/test'
import { createSession } from './helpers'
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

/** durable 回复到达之前，界面上是否已经出现过增量文本（乐观 assistant 气泡）。 */
async function sawStreamingBubble(page: Page): Promise<boolean> {
  return page.evaluate(
    () => (window as unknown as { __sawStreamingBubble?: boolean }).__sawStreamingBubble ?? false,
  )
}

test('delta 真的到达浏览器，且最终收敛成一条消息（不重复、不残留）', async ({
  page,
  request,
}) => {
  const meta = await (await request.get(`${BASE}/api/meta`)).json()
  test.skip(meta.features?.deltas !== 1, '内核没有声明 deltas 能力（需要 AVID_E2E_STREAM=1）')

  await page.addInitScript(() => {
    const state = window as unknown as {
      __deltaFrames: number
      __sawStreamingBubble: boolean
      __durableReply: boolean
    }
    state.__deltaFrames = 0
    state.__sawStreamingBubble = false
    state.__durableReply = false
    const original = window.fetch.bind(window)

    // 乐观 assistant 气泡的稳定特征：assistant 卡片带 `mr-auto`，而 `aria-live`
    // **只加在 durable** 的那张上（EntryRow 的刻意设计）。所以
    // `article.mr-auto:not([aria-live])` 就是正在生成的那一条。
    const sample = () => {
      if (state.__durableReply) return
      const bubble = document.querySelector(
        '[role="log"] article.mr-auto:not([aria-live])',
      )
      if (bubble && (bubble.textContent ?? '').trim()) state.__sawStreamingBubble = true
    }

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
            if (frame.includes('event: assistant_delta')) {
              state.__deltaFrames += 1
              // 每收到一帧就采一次样：delta 的可见性是本用例的核心，不能只看最终文本。
              sample()
            }
            if (frame.includes('event: assistant_message') && frame.includes('做')) {
              state.__durableReply = true
            }
          }
        }
      })()

      const pump = () => {
        sample()
        if (!state.__durableReply) requestAnimationFrame(pump)
      }
      requestAnimationFrame(pump)

      // 交回给应用的是 tee 出来的另一支，内容与状态码、头都保持一致。
      return new Response(app, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      })
    }
  })

  const created = await createSession(request, { name: `流式验证-${Date.now()}` })
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

  // **可见性**：帧到达还不够，必须证明界面上在 durable 回复之前就出现过增量文本。
  // 只推了一帧时可能来不及被采样到（模型整段一次吐完），那种情况不构成流式，
  // 因此仅在观察到多帧时强制断言。
  const frames = await deltaFrames(page)
  if (frames > 1) {
    expect(await sawStreamingBubble(page), 'durable 回复到达前应已渲染增量文本').toBe(true)
  }

  // 收敛：乐观条目必须被 durable 消息取代，最终文本只出现一次（I12）。
  const occurrences = await log.getByText(REPLY, { exact: true }).count()
  expect(occurrences, '最终文本只应出现一次（乐观条目已被 durable 消息替换）').toBe(1)
})
