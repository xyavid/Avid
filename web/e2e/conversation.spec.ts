import { expect, test } from '@playwright/test'

import { newSessionIn } from './helpers'

/**
 * 交互冒烟：起一个会话 → 提交 → UI 上批准 → 看到最终答复。
 *
 * 前置（与 smoke.spec.ts 相同）：`AVID_E2E=1` 且内核已起在 127.0.0.1:8765。
 * 为了不依赖真实模型，配合 `docs/guide/web-ui.md` 里的脚本化服务使用
 * （`Services(chat=ScriptedChat(...), tool_registry=...)`）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')
// 两个用例有先后依赖（检查器要看第一个用例产生的工具调用），串行执行。
test.describe.configure({ mode: 'serial' })

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'

test('提交 → 审批 → 完成：键盘可完成主任务且无页面错误', async ({ page, request }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })

  await page.goto(`${BASE}/sessions`)

  // 在导航树的工作区文件夹里新建会话（在哪个文件夹点 ＋ 就建在哪个工作区）
  await newSessionIn(page, request)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

  // 输入并发送
  const composer = page.getByLabel('输入指令，Enter 发送，Shift+Enter 换行')
  await composer.fill('跑一下')
  await composer.press('Enter')

  // 审批卡出现（默认焦点在拒绝上）
  await expect(page.getByText('待决审批').first()).toBeVisible({ timeout: 15_000 })
  const allow = page.getByRole('button', { name: '允许一次' })
  await expect(allow).toBeVisible()
  await allow.click()

  // 最终答复与工具卡
  await expect(page.getByText('做完了')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('CALL').first()).toBeVisible()

  // 键盘用户不该被丢在 body 上
  const focused = await page.evaluate(() => document.activeElement?.tagName ?? '')
  expect(focused).not.toBe('')

  expect(errors).toEqual([])
})

test('检查器可就地查看工具输出（全文 / diff / 原始 JSON）', async ({ page }) => {
  // 用接口定位一个有条目的会话，避免依赖列表里的点击顺序（那是上一个用例的事）。
  const listed = await page.request.get(`${BASE}/api/sessions`)
  const sessions: { id: string; message_count: number }[] = (await listed.json()).sessions
  const target = sessions.find((item) => item.message_count > 0)
  expect(target, '需要一个包含工具调用的会话（先跑上一个用例）').toBeTruthy()
  await page.goto(`${BASE}/sessions/${target?.id}`)

  const call = page.getByRole('button').filter({ hasText: 'CALL' }).first()
  await expect(call).toBeVisible({ timeout: 10_000 })
  await call.click()

  // 展开后的工具卡自带「查看」：开检查器（就地切换，不进 URL 历史）。
  const card = page.locator('section.sketch-card').filter({ hasText: 'CALL' }).first()
  await card.getByRole('button', { name: '查看' }).click()
  await expect(page.getByRole('tab', { name: '全文' })).toBeVisible()
  // 全文里有工具返回内容（限定在检查器内：时间线的工具卡也有同一段文本）
  const inspector = page.getByRole('complementary', { name: '全文' })
  await expect(inspector.getByText('bash ok')).toBeVisible()
  await page.getByRole('tab', { name: '原始 JSON' }).click()
  await expect(inspector.getByText('"arguments"')).toBeVisible()
})
