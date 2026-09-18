import { expect, test } from '@playwright/test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/**
 * 阶段 18 前端的端到端：新建会话前选工作区、提交前选权限模式，两者都真的进了请求体。
 *
 * 前置：`AVID_E2E=1`，且内核以 `dev/tmp/e2e_server.py` 起（**单工作区模式**，注册表被
 * `AVID_HOME` 隔离到临时目录）。只有单工作区时这个用例也要能过：它会先登记第二个
 * 工作区（临时目录），于是「选中的是不是我选的那个」才有意义。
 *
 * 断言的是**请求体**而不是页面文案：这两个选择器的价值全在「值有没有跟着请求走」，
 * 页面显示成什么都可能对。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const WORKSPACE_LABEL = '工作区'
const PERMISSION_LABEL = '权限模式'
const COMPOSER_LABEL = '输入指令，Enter 发送，Shift+Enter 换行'

// 导航列只在宽档展开（<1280 收成图标轨，会话列表不挂载），显式钉住视口而不是靠默认值。
test.use({ viewport: { width: 1440, height: 900 } })

test('选工作区建会话、选权限模式提交，两个值都进请求体', async ({ page, request }) => {
  const stamp = Date.now()
  // 第二个工作区用临时目录：会话会落在它自己的 <tmp>/.avid/sessions 下，不污染仓库。
  const extraRoot = mkdtempSync(join(tmpdir(), 'avid-e2e-ws-'))
  const extraName = `e2e工作区-${stamp}`
  const registered = await request.post(`${BASE}/api/workspaces`, {
    data: { path: extraRoot, name: extraName },
  })
  expect(registered.status(), `登记工作区失败：${await registered.text()}`).toBe(201)
  const extraId = (await registered.json()).id as string

  try {
    await page.goto(`${BASE}/sessions`)

    // ---- 1. 新建会话必须先选工作区 ----
    const selector = page.getByLabel(WORKSPACE_LABEL)
    await expect(selector).toBeVisible({ timeout: 10_000 })
    await expect(selector.locator('option', { hasText: extraName })).toHaveCount(1)

    const createResponse = page.waitForResponse(
      (res) => res.request().method() === 'POST' && res.url().endsWith('/api/sessions'),
    )
    await selector.selectOption(extraId)
    await page.getByRole('button', { name: '新建会话' }).click()
    const created = await createResponse

    // 选中的那个工作区必须跟着 POST /api/sessions 走（服务端 extra=forbid，字段名也不能错）。
    expect(created.request().postDataJSON()).toMatchObject({ workspace: extraId })
    expect(created.status(), `建会话失败：${await created.text()}`).toBe(201)
    const sessionId = ((await created.json()) as { id: string }).id
    await page.waitForURL(new RegExp(`/sessions/${sessionId}$`), { timeout: 10_000 })

    // 详情里显示归属的工作区名。
    await expect(page.getByText(`工作区：${extraName}`).first()).toBeVisible({ timeout: 10_000 })

    // ---- 2. 权限模式随 POST /runs 发出 ----
    const permission = page.getByLabel(PERMISSION_LABEL)
    await expect(permission).toBeVisible()
    // 缺省不该是 system：工作区没登记默认权限时两侧都回落到 strict。
    await expect(permission).toHaveValue('strict')

    const runRequest = page.waitForRequest(
      (req) => req.method() === 'POST' && req.url().includes(`/api/sessions/${sessionId}/runs`),
    )
    await permission.selectOption('system')
    const composer = page.getByLabel(COMPOSER_LABEL)
    await composer.fill(`权限模式用例-${stamp}`)
    await composer.press('Enter')
    const started = await runRequest

    // 三档的值就是服务端的 Literal，非法值会被 422 挡掉。
    expect(started.postDataJSON()).toMatchObject({
      permission: 'system',
      prompt: `权限模式用例-${stamp}`,
    })

    // 系统级下这条工作区内的 bash 不再问：不再出现审批条，消息直接落到时间线。
    await expect(page.getByRole('log').getByText(`权限模式用例-${stamp}`)).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.getByRole('button', { name: '允许一次' })).toHaveCount(0)
  } finally {
    rmSync(extraRoot, { recursive: true, force: true })
  }
})
