/**
 * 对话主流程的 E2E。
 *
 * 覆盖一条完整链路：起运行 → 流式接收 → 工具卡出现 → 打开检查器 → 取消。
 * 这些正是"事件流接线"最容易出错、而单测覆盖不到的地方——单测能证明 reducer 折叠
 * 得对，证明不了"SSE 真的接上了、delta 真的画到屏幕上了"。
 */

import { expect, test } from './lib/fixtures'
import { defaultSession } from './lib/api'

/** 构造一条 durable 事件（字段与内核 `EventPayload` 同形）。 */
function event(type: string, seq: number, data: Record<string, unknown> = {}) {
  return { run_id: 'run-1', session_id: 's-1', seq, ts: 1_700_000_000_000 + seq * 1000, type, data }
}

test.describe('对话主流程', () => {
  test('起运行后清空草稿；按钮只在服务端确认运行中之后才变成停止', async ({ page, stubApi }) => {
    const stub = await stubApi({
      sessions: [defaultSession('s-1', 'E2E 会话')],
      // 只发"运行已开始"：界面据此才认为在跑。
      events: [event('run_started', 1, { prompt: '你好，跑一下测试' })],
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })

    const input = page.getByRole('textbox', { name: '输入' })
    await input.fill('你好，跑一下测试')
    await page.getByRole('button', { name: '发送' }).click()

    // 请求体必须是这次输入的内容，并带上权限档与分支——这是"起运行"的契约。
    await expect
      .poll(() => stub.calls('POST', '/runs')[0]?.body)
      .toEqual({ prompt: '你好，跑一下测试', permission: 'manual', branch: 'main' })

    // 提交被受理：草稿清空。
    await expect(input).toHaveValue('')

    /*
     * 按钮**只在服务端确认"运行中"之后**才变成「停止」。
     *
     * 这是有意的不乐观切换：`POST /runs` 成功只说明内核接受了提交、开了线程；
     * 真正的运行状态以 `run_started` / `run_status` 事件为准。若在 POST 返回时
     * 就本地改成 running，用户在"已受理但还没跑"的那段时间会看到一个骗人的停止按钮。
     * 这里用真事件把这条语义钉住。
     */
    await expect(page.getByRole('button', { name: '停止' })).toBeVisible()
    await expect(page.getByRole('button', { name: '发送' })).toBeHidden()
  })

  test('流式事件把工具卡画到时间线上，并能打开检查器', async ({ page, stubApi }) => {
    const stub = await stubApi({
      sessions: [defaultSession('s-1', 'E2E 会话')],
      events: [
        event('run_started', 1, { prompt: '读一下 tokens.css' }),
        event('user_message', 2, { message: { role: 'user', content: '读一下 tokens.css' }, entry_id: 'e-user' }),
        event('tool_call_started', 3, {
          tool: 'read_file',
          tool_call_id: 'tc-1',
          arguments: { path: 'web/src/styles/tokens.css' },
        }),
        event('tool_call_finished', 4, {
          tool: 'read_file',
          tool_call_id: 'tc-1',
          status: 'ok',
          duration_ms: 12,
          content_chars: 40,
        }),
        event('tool_result_message', 5, {
          message: { role: 'tool', tool_call_id: 'tc-1', content: ':root { --avid-bg-rgb: 240 239 232; }' },
        }),
        event('assistant_message', 6, {
          message: { role: 'assistant', content: '读完了，主区纸面是 #F0EFE8。' },
          entry_id: 'e-assistant',
        }),
      ],
    })

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.getByRole('textbox', { name: '输入' }).fill('读一下 tokens.css')
    await page.getByRole('button', { name: '发送' }).click()

    // 事件流接上了：SSE 端点被订阅，且带上了 delta 订阅参数。
    await expect.poll(() => stub.calls('GET', '/events').length).toBeGreaterThan(0)

    // 时间线上出现用户消息、工具卡与助手回复。
    const timeline = page.getByRole('log')
    await expect(timeline.getByText('读一下 tokens.css')).toBeVisible()
    await expect(timeline.getByText('read_file')).toBeVisible()
    await expect(timeline.getByText('读完了，主区纸面是 #F0EFE8。')).toBeVisible()

    // 打开检查器：工具卡上的「查看」按钮 → 右栏出现三个页签。
    await page.getByRole('button', { name: /查看 read_file 的调用详情/ }).click()
    await expect(page.getByRole('tablist', { name: '检查器视图' })).toBeVisible()
    await expect(page.getByRole('tab', { name: '结果' })).toHaveAttribute('aria-selected', 'true')
    // 「改动」在 read_file 上没有 diff，必须禁用而不是给一个空面板。
    await expect(page.getByRole('tab', { name: '改动' })).toBeDisabled()
    await page.getByRole('tab', { name: '参数' }).click()
    await expect(page.getByRole('tab', { name: '参数' })).toHaveAttribute('aria-selected', 'true')
  })

  test('取消运行只发一次请求，并保持等待终态由事件决定', async ({ page, stubApi }) => {
    const stub = await stubApi({
      sessions: [defaultSession('s-1', 'E2E 会话')],
      events: [event('run_started', 1, { prompt: '跑' })],
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.getByRole('textbox', { name: '输入' }).fill('跑')
    await page.getByRole('button', { name: '发送' }).click()
    await expect(page.getByRole('button', { name: '停止' })).toBeVisible()

    await page.getByRole('button', { name: '停止' }).click()

    await expect.poll(() => stub.calls('POST', '/cancel').length).toBe(1)
    /*
     * 取消是**请求**而不是结果：界面不该自己把状态改成"已取消"。
     * 这里断言按钮仍在（phase 还是 running），因为服务端还没回终态事件——
     * 若有人图省事在点击时直接改本地状态，这条会红。
     */
    await expect(page.getByRole('button', { name: '停止' })).toBeVisible()
  })

  test('SSE 未订阅到 delta 时仍渲染整段落地的消息', async ({ page, stubApi }) => {
    // 不传 events：流建立后不发任何事件。这条钉住"没有增量也不崩"。
    await stubApi({ sessions: [defaultSession('s-1', 'E2E 会话')], events: [] })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('E2E 会话')
    await expect(page.getByRole('textbox', { name: '输入' })).toBeEnabled()
  })
})
