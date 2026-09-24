import { expect, test } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

/**
 * 视觉基线：把「新语言长什么样」冻成能报警的图。
 *
 * 三条设计取舍，每条都是被前一次失败逼出来的：
 *
 * 1. **按表面（surface）截图，不截整页**。整页会把导航列一起截进来，而导航列的内容是
 *    「这套 e2e 跑到现在攒下的所有会话」——跑第二遍就不一样，基线会天天报红。
 *    表面截图（对话卡 / 面板 / 空态）只取决于本用例自己造的数据。
 * 2. **时钟固定在造数据之后**（`clock.setFixedTime`）：导航行的「几分钟前」与审批卡的
 *    「有效期至 …」都以 `Date.now()` 为基准，不固定的话两遍之间就会漂移。
 * 3. **遮罩 ID 标签**：会话 id 与运行 id 是随机的，而它们是**数据**不是观感。
 *    遮罩用语义类名（`.id-tag`），不按坐标。基线落在 Playwright 默认的 `e2e/visual.spec.ts-snapshots/`。
 *
 * 容差 `maxDiffPixels: 3` 是 23a 实测的噪声下限（同一份构建截两遍差 3 像素、
 * 单通道 ≤4/255，位置固定为导航列第一个条目按钮的 1px 左边框）。写 0 会让同一份构建
 * 自己报红——门禁随即退化成「每次改动都点一下同意」。见 design §8.9。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const COMPOSER = '输入指令，Enter 发送，Shift+Enter 换行'
const MASK = '.id-tag, .id-tag-empty'

/**
 * 对话卡头部那行「工作区：<名字>」也要遮。
 *
 * 同一条理由的上半截已经在头部注释里写了：会话 id / 运行 id 是**数据**不是观感，所以遮罩。
 * 工作区名同理——它由**这个进程**注册的工作区决定，而 e2e 服务把工作区根指到
 * `tempfile.mkdtemp(prefix='avid-e2e-workspace-')`，于是那行里带一个每次起服务都不同的随机
 * 后缀（实测 `-5cn8fs6s` 与 `-1690olcv` 差 50×8 像素就足以让基线报红，且报的是"对话卡变了"
 * 这种与改动无关的错）。按文案定位而不是按坐标：文案在字典里，改文案时遮罩失效会让基线
 * 重新报红（自曝），而不是静默放过。
 */
const WORKSPACE_NAME = (page: Page) => page.getByText(/^工作区：/)

/** 截图选项：容差按实测噪声下限，动效冻掉（否则会截到过渡的中间帧）。 */
const SHOT = { maxDiffPixels: 3, animations: 'disabled' } as const

test.describe.configure({ mode: 'serial' })

let finishedId = ''
let pendingId = ''
let approvalExpiresAt = 0

async function json<T>(response: { ok(): boolean; json(): Promise<unknown>; text(): Promise<string>; url(): string }): Promise<T> {
  if (!response.ok()) throw new Error(`${response.url()} → ${await response.text()}`)
  return (await response.json()) as T
}

/** 起一次运行并等它挂起在审批上（脚本模型的第一次调用要落在 REVIEW 上，见 `e2e/README.md`）。 */
async function startRun(request: APIRequestContext, sessionId: string, prompt: string): Promise<{
  runId: string
  approvalId: string
  expiresAt: number
}> {
  const run = await json<{ run_id: string }>(
    await request.post(`${BASE}/api/sessions/${sessionId}/runs`, {
      data: { prompt, branch: 'main', auto_approve: false, permission: 'manual' },
    }),
  )
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const { approvals } = await json<{
      approvals: { approval_id: string; decision: string | null; expires_at: number }[]
    }>(
      await request.get(`${BASE}/api/runs/${run.run_id}/approvals`),
    )
    const waiting = approvals.find((item) => item.decision === null)
    if (waiting) {
      return { runId: run.run_id, approvalId: waiting.approval_id, expiresAt: waiting.expires_at }
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error('迟迟没有待决审批')
}

test.beforeAll(async ({ request }) => {
  const { workspaces } = await json<{ workspaces: { id: string; is_default?: boolean }[] }>(
    await request.get(`${BASE}/api/workspaces`),
  )
  const workspace = (workspaces.find((item) => item.is_default) ?? workspaces[0])?.id
  if (!workspace) throw new Error('服务端没有绑定任何工作地点')

  const create = async (name: string) =>
    (
      await json<{ id: string }>(
        await request.post(`${BASE}/api/sessions`, { data: { workspace, name } }),
      )
    ).id

  // 已完成的一个：批准那次工具调用，等它跑完（有正文的那一轮）。
  finishedId = await create('视觉基线 · 已完成')
  const first = await startRun(request, finishedId, '视觉基线：请先跑一次工具再给结论')
  await request.post(`${BASE}/api/runs/${first.runId}/approvals/${first.approvalId}`, {
    data: { decision: 'allow' },
  })
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const detail = await json<{ active_run_id: string | null; message_count: number }>(
      await request.get(`${BASE}/api/sessions/${finishedId}`),
    )
    if (!detail.active_run_id && detail.message_count > 1) break
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  // 从链尾分叉一条，分支选择器才有两个可选项。
  const page = await json<{ entries: { entry_id: string }[] }>(
    await request.get(`${BASE}/api/sessions/${finishedId}/entries?branch=main&limit=50&order=desc`),
  )
  const tip = page.entries[0]
  if (tip) await request.post(`${BASE}/api/sessions/${finishedId}/branches`, { data: { at: tip.entry_id } })

  // 待审批的一个：起了就不答。
  pendingId = await create('视觉基线 · 待审批')
  const pending = await startRun(request, pendingId, '视觉基线：待审批')
  approvalExpiresAt = pending.expiresAt
})

/**
 * 冻结页面时钟。**必须在 `page.goto` 之前调用**：组件是在渲染那一刻用 `Date.now()`
 * 算出文案的，导航之后再冻只会冻住一个已经算好的值——审批卡上那句「还有 30 秒」会随
 * 真实秒针跳，基线于是随机报红（实测踩过：只有一个小字形不同，就是那个秒数）。
 *
 * 时刻要**相对数据**取，不能取绝对常数：`expiresAt` 由服务端按真实时间给出，冻在常数上
 * 时差值会随真实时钟漂移。默认冻在"刚刚"，审批那一条冻在"还有整 1 小时"。
 */
async function freeze(page: Page, at: Date = new Date(Date.now() - 5_000)) {
  await page.clock.setFixedTime(at)
}

/** 冻动效 + 遮罩，然后截某一个表面。 */
async function shoot(page: Page, target: ReturnType<Page['locator']>, name: string) {
  await page.addStyleTag({
    content: '* { transition: none !important; animation: none !important }',
  })
  await page.evaluate(() => document.fonts.ready)
  await expect(target).toHaveScreenshot(name, {
    ...SHOT,
    mask: [page.locator(MASK), WORKSPACE_NAME(page)],
  })
}

test('视觉基线：会话落点页', async ({ page }) => {
  await freeze(page)
  await page.goto(`${BASE}/sessions`)
  const main = page.locator('section.surface-main')
  await expect(main).toBeVisible()
  await shoot(page, main, 'landing.png')
})

test('视觉基线：对话卡（时间线 + 输入条）', async ({ page }) => {
  await freeze(page)
  await page.goto(`${BASE}/sessions/${finishedId}`)
  await expect(page.getByLabel(COMPOSER)).toBeVisible()
  await expect(page.getByRole('log').getByText('做完了', { exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await shoot(page, page.locator('section.surface-main'), 'conversation.png')
})

test('视觉基线：审批待决', async ({ page }) => {
  // 冻在「有效期还有整 1 小时」的时刻：那一行的文案因此是「1 小时后」，与真实时钟无关。
  await freeze(page, new Date(approvalExpiresAt - 3_600_000))
  await page.goto(`${BASE}/sessions/${pendingId}`)
  await expect(page.getByRole('button', { name: '允许一次' })).toBeVisible({ timeout: 15_000 })
  await shoot(page, page.locator('section.surface-main'), 'approval.png')
})

test('视觉基线：技能目录面板', async ({ page }) => {
  await freeze(page)
  await page.goto(`${BASE}/skills`)
  const panel = page.locator('section.surface-panel')
  await expect(panel).toBeVisible()
  await shoot(page, panel, 'skills-panel.png')
})
