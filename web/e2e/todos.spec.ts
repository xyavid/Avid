import { expect, test } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

import { createSession } from './helpers'

/**
 * 输入条上方那块**待办清单**的回归（阶段 27）。
 *
 * 它要守住三件事：
 *   1. **位置**：清单常驻在输入条**正上方**，而且**不**在时间线那个滚动容器里——否则消息一滚
 *      它就跟走了，"边写边看进度"不成立；
 *   2. **没有清单就不渲染**：从没调过 `todo_write` 的会话里，输入条上方不该留一条空框
 *      （"没有计划"与"计划是空的"是两件事，见 `features/conversation/lib/todos.ts`）；
 *   3. **是当前计划**：条目文字与进度摘要要和那次 `todo_write` 提交的内容一致。
 *
 * 数据来源是会话条目里 `todo_write` 的调用参数，所以正向用例只需要"有一个调过它的会话"。
 * 那份脚本模型在仓库外（`dev/` 不入库），它不一定调 `todo_write`：**没有就跳过**，不伪造
 * 断言——空库上红的那条是环境，不是改动。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const COMPOSER = '输入指令，Enter 发送，Shift+Enter 换行'
const PANEL = '待办清单'

interface TodoItem {
  content: string
  status: string
}

interface Found {
  sessionId: string
  todos: TodoItem[]
}

/** 扫描已有会话，找**最后一次** `todo_write` 提交的清单；没有就返回 null。 */
async function findTodoSession(request: APIRequestContext): Promise<Found | null> {
  const listed = await request.get(`${BASE}/api/sessions`)
  const { sessions } = (await listed.json()) as {
    sessions: { id: string; message_count: number }[]
  }
  for (const session of sessions) {
    if (session.message_count === 0) continue
    const response = await request.get(
      `${BASE}/api/sessions/${session.id}/entries?branch=main&limit=100&order=desc`,
    )
    if (!response.ok()) continue
    const { entries } = (await response.json()) as { entries: { message?: unknown }[] }
    for (const entry of entries) {
      const message = entry.message as { role?: string; tool_calls?: unknown } | undefined
      const calls = Array.isArray(message?.tool_calls) ? message.tool_calls : []
      for (const call of calls) {
        const fn = (call as { function?: { name?: string; arguments?: string } }).function
        if (fn?.name !== 'todo_write') continue
        const parsed = JSON.parse(fn.arguments ?? '{}') as { todos?: TodoItem[] }
        if (Array.isArray(parsed.todos) && parsed.todos.length > 0) {
          return { sessionId: session.id, todos: parsed.todos }
        }
      }
    }
  }
  return null
}

async function openEmptySession(page: Page, request: APIRequestContext): Promise<void> {
  const created = await createSession(request, { name: `待办用例-${Date.now()}` })
  expect(created.status(), `建会话失败：${await created.text()}`).toBe(201)
  const { id } = (await created.json()) as { id: string }
  await page.goto(`${BASE}/sessions/${id}`)
  await expect(page.getByLabel(COMPOSER)).toBeVisible()
}

test('没有待办清单的会话：输入条上方不渲染面板（也不留间距）', async ({ page, request }) => {
  await openEmptySession(page, request)
  await expect(page.getByRole('region', { name: PANEL })).toHaveCount(0)
  // 输入条照常在视口里（底栏少一块时不该被挤出视口）
  const box = await page.getByLabel(COMPOSER).boundingBox()
  const height = page.viewportSize()?.height ?? 0
  if (!box) throw new Error('输入条没有几何信息')
  expect(box.y + box.height, '输入条整体在视口内').toBeLessThanOrEqual(height + 1)
})

test('有待办清单时：面板在输入条上方、条目与进度一致、可折叠', async ({ page, request }) => {
  const found = await findTodoSession(request)
  test.skip(!found, '这份脚本模型没有调过 todo_write（服务端模型需要会调它）')
  await page.goto(`${BASE}/sessions/${found!.sessionId}`)

  const panel = page.getByRole('region', { name: PANEL })
  const composer = page.getByLabel(COMPOSER)
  await expect(panel, '清单常驻在输入条上方').toBeVisible()

  const panelBox = await panel.boundingBox()
  const composerBox = await composer.boundingBox()
  if (!panelBox || !composerBox) throw new Error('面板或输入条没有几何信息')
  expect(panelBox.y + panelBox.height, '位置：面板整体在输入条上方').toBeLessThanOrEqual(
    composerBox.y + 1,
  )
  await expect(
    page.locator('.scroll-area').getByRole('region', { name: PANEL }),
    '不落在时间线的滚动容器里（否则消息一滚它就走了）',
  ).toHaveCount(0)

  // 是**当前**计划：最后一次提交的条目逐条在、进度数字与它一致。
  const total = found!.todos.length
  const done = found!.todos.filter((item) => item.status === 'completed').length
  await expect(panel.getByText(`已完成 ${done}/${total}`)).toBeVisible()
  await expect(panel.getByText(found!.todos[0]!.content, { exact: true })).toBeVisible()

  // 可折叠：收起后条目不在 DOM，展开又回来（状态进界面域，切会话不丢）。
  const toggle = panel.getByRole('button', { name: new RegExp(`^${PANEL}`) })
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(panel.getByRole('list')).toHaveCount(0)
  await expect(panel.getByText(`已完成 ${done}/${total}`), '收起后仍看得到进度').toBeVisible()
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(panel.getByRole('list')).toBeVisible()
})
