import { expect, test } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

/**
 * F4 的端到端：从某一轮分叉 → 在新链上继续 → 切回主线，两边的历史各自独立。
 *
 * 需要 `AVID_E2E=1` 且内核已起（脚本模型即可，见 docs/guide/web-ui.md）。
 * 前置历史用接口造（`auto_approve: true`），只有「在分支上继续」这一步走界面——
 * 那正是要验证的接线（composer → POST /runs 带 branch）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const COMPOSER_LABEL = '输入指令，Enter 发送，Shift+Enter 换行'

/** 起一次运行并等到注册表真的释放会话（终态事件先于释放，紧接着 POST 会撞 409）。 */
async function runToIdle(request: APIRequestContext, sessionId: string, prompt: string) {
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
      if (run.status !== 'running' && run.status !== 'awaiting_approval' && !detail.active_run_id) {
        return
      }
      await new Promise((resolve) => setTimeout(resolve, 50))
    }
    return
  }
  throw new Error('反复遇到 run_busy：注册表没有释放会话')
}

async function branchNames(request: APIRequestContext, sessionId: string): Promise<string[]> {
  const page = await request.get(`${BASE}/api/sessions/${sessionId}/branches`)
  return ((await page.json()).branches as { name: string }[]).map((item) => item.name)
}

async function openSession(page: Page, sessionId: string, marker: string) {
  await page.goto(`${BASE}/sessions/${sessionId}`)
  await expect(page.getByRole('log').getByText(marker)).toBeVisible({ timeout: 10_000 })
}

test('从某一轮分叉、在分支上继续、再切回主线', async ({ page, request }) => {
  const stamp = Date.now()
  const first = `主线第一句-${stamp}`
  const second = `主线第二句-${stamp}`
  const created = await request.post(`${BASE}/api/sessions`, { data: { name: `分支-${stamp}` } })
  const sessionId = (await created.json()).id as string
  await runToIdle(request, sessionId, first)
  await runToIdle(request, sessionId, second)

  const entriesOf = async (branch: string) => {
    const page = await request.get(
      `${BASE}/api/sessions/${sessionId}/entries?branch=${branch}&order=asc`,
    )
    return ((await page.json()).entries as { entry_id: string }[]).map((entry) => entry.entry_id)
  }

  await openSession(page, sessionId, second)
  const log = page.getByRole('log')
  const selector = page.getByRole('button', { name: 'main', exact: true })
  await expect(selector, '新建会话的隐式默认分支是 main').toBeVisible()
  await expect(page.getByRole('button', { name: '从链尾分叉' })).toBeVisible()

  // 在**第一条**用户消息处「从此处分支」：新链只剩这一条
  await log.getByRole('button', { name: '从此处分支' }).first().click()
  await expect(
    page.getByRole('button', { name: 'b2', exact: true }),
    '分叉后自动切到新分支',
  ).toBeVisible({ timeout: 10_000 })
  await expect(log.getByText(first)).toBeVisible()
  await expect(log.getByText(second), '分叉点之后的内容不属于新链').toHaveCount(0)
  expect(await branchNames(request, sessionId)).toEqual(['main', 'b2'])
  const mainBefore = await entriesOf('main')

  // 在新链上继续：走界面提交，branch 必须跟着请求走
  const onBranch = `分支上的回复-${stamp}`
  const composer = page.getByLabel(COMPOSER_LABEL)
  await composer.fill(onBranch)
  await composer.press('Enter')
  const allow = page.getByRole('button', { name: '允许一次' })
  await expect(allow).toBeVisible({ timeout: 15_000 })
  await allow.click()
  await expect(log.getByText(onBranch)).toBeVisible({ timeout: 15_000 })

  // 切回主线：主线没有那句新话
  await page.getByRole('button', { name: 'b2', exact: true }).click()
  await page.getByRole('button', { name: /^main/ }).click()
  await expect(log.getByText(second)).toBeVisible({ timeout: 10_000 })
  await expect(log.getByText(onBranch), '分支上的消息不该出现在主线').toHaveCount(0)

  // 服务端的两条链也确实各自独立。条目数不能写死：脚本模型第一轮会调工具
  // （user + 带 tool_calls 的 assistant + tool 结果 + 收尾 assistant），第二轮看到
  // 历史里已有 tool 消息就直接收尾，所以每轮条数不同。这里只断言关系。
  const mainAfter = await entriesOf('main')
  expect(mainAfter, '分支上的运行不该动主线').toEqual(mainBefore)
  const side = await entriesOf('b2')
  expect(side[0], '分叉点之前共享同一条链').toBe(mainBefore[0])
  expect(side.slice(1), '分叉点之后各自私有').not.toContain(mainBefore[1])
  expect(new Set(side).size, '分支链上没有重复条目').toBe(side.length)
})
