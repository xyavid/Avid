import { expect, test } from '@playwright/test'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'

import { createSession, workspaceFolder } from './helpers'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'

/**
 * 工作区的端到端：导航树里按文件夹建会话、提交前选权限模式、以及界面新增工作区。
 *
 * 前置：`AVID_E2E=1`，且内核以 `dev/tmp/e2e_server.py` 起（注册表被 `AVID_HOME` 隔离到
 * 临时目录）。只有单工作区时这些用例也要能过：它们会先登记第二个工作区（临时目录），
 * 于是「建在哪个工作区」才有意义。
 *
 * 断言的是**请求体**而不是页面文案：这些交互的价值全在「值有没有跟着请求走」，
 * 页面显示成什么都可能对。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
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

    // ---- 1. 工作区像文件夹：在它的文件夹里点 ＋ 建会话 ----
    const folder = workspaceFolder(page, extraName)
    await expect(folder, '新登记的工作区要出现在导航树里').toBeVisible({ timeout: 10_000 })
    await expect(folder).toHaveAttribute('aria-expanded', 'false') // 空文件夹默认收起
    await folder.click()
    await expect(folder).toHaveAttribute('aria-expanded', 'true')
    // 空文件夹里给出可执行提示（scope 到这一行：别的文件夹与主区也都有这句话）
    const row = page.getByRole('listitem').filter({ has: folder })
    await expect(row.getByText('还没有会话')).toBeVisible()

    const createResponse = page.waitForResponse(
      (res) => res.request().method() === 'POST' && res.url().endsWith('/api/sessions'),
    )
    await page.getByRole('button', { name: `在「${extraName}」新建会话` }).click()
    const created = await createResponse

    // 点的是哪个文件夹，POST /api/sessions 就带哪个工作区（服务端 extra=forbid，字段名不能错）。
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

/** 服务端候选列表（新增工作区用例要反复读它）。 */
async function workspacesOf(
  request: import('@playwright/test').APIRequestContext,
): Promise<{ id: string; root: string; name: string | null }[]> {
  const response = await request.get(`${BASE}/api/workspaces`)
  return ((await response.json()) as { workspaces: { id: string; root: string; name: string | null }[] })
    .workspaces
}

/**
 * 新增工作区：点按钮 → 选文件夹 → 登记 → 切过去。
 *
 * 真对话框没法在无人值守的测试里点，所以服务端用 `AVID_PICKER_CMD` 把选择器换成
 * "读一个文件里的路径"：写路径 = 用户选了它，写空 = 用户点了取消。这条用例覆盖需求里
 * 的四条验收：取消不变更、不存在/重复给提示且不重复添加、成功后列表与持久化都可见。
 */
test('新增工作区：取消不变更，选择后登记并切过去，重复时提示且不重复添加', async ({
  page,
  request,
}) => {
  const pickFile = process.env.AVID_E2E_PICK_FILE
  test.skip(!pickFile, '需要 AVID_E2E_PICK_FILE（服务端也用它接管选择器）')

  const folder = mkdtempSync(join(tmpdir(), 'avid-e2e-pick-'))
  await page.goto(`${BASE}/sessions`)
  const addButton = page.getByRole('button', { name: '新增工作区…' }) // 导航面板右上角的 ＋
  await expect(addButton).toBeVisible({ timeout: 10_000 })

  try {
    // ---- 1. 取消：不做任何变更（也没有发出登记请求）----
    writeFileSync(pickFile!, '')
    let addCalls = 0
    const countAdds = (req: import('@playwright/test').Request) => {
      if (req.method() === 'POST' && req.url().endsWith('/api/workspaces')) addCalls += 1
    }
    page.on('request', countAdds)
    const before = await workspacesOf(request)
    await addButton.click()
    await expect(page.getByText(/已添加工作区/)).toHaveCount(0)
    expect((await workspacesOf(request)).length).toBe(before.length)
    expect(addCalls).toBe(0)
    page.off('request', countAdds)

    // ---- 2. 选择：请求体带 path，成功后切到它 ----
    writeFileSync(pickFile!, folder)
    const [addRequest] = await Promise.all([
      page.waitForRequest(
        (req) => req.method() === 'POST' && req.url().endsWith('/api/workspaces'),
      ),
      addButton.click(),
    ])
    expect((addRequest.postDataJSON() as { path: string }).path).toBe(folder)
    await expect(page.getByText(/已添加工作区/)).toBeVisible({ timeout: 10_000 })

    const added = (await workspacesOf(request)).find((item) => item.root === folder)
    expect(added, '新增的工作区必须出现在服务端列表里').toBeTruthy()
    // 切到它 = 在导航树里展开它（新加进来的文件夹默认展开，好让人看见结果）。
    const addedFolder = workspaceFolder(page, basename(folder))
    await expect(addedFolder).toBeVisible({ timeout: 10_000 })
    await expect(addedFolder).toHaveAttribute('aria-expanded', 'true')

    // ---- 3. 重复：明确提示、不重复添加、仍切到已有的那个 ----
    writeFileSync(pickFile!, folder)
    await addButton.click()
    await expect(page.getByText(/已经在工作区列表里/)).toBeVisible({ timeout: 10_000 })
    const again = (await workspacesOf(request)).filter((item) => item.root === folder)
    expect(again).toHaveLength(1)
    await expect(addedFolder).toHaveAttribute('aria-expanded', 'true')
  } finally {
    rmSync(folder, { recursive: true, force: true })
  }
})

/**
 * 按会话搜索：只留匹配项、没有匹配给提示、清除后恢复。
 *
 * 搜索是纯客户端的（服务端没有全文检索），所以这里断言的是导航树的可见性，
 * 不涉及任何网络请求——顺带说明它不该因为过滤而多发一次会话列表请求。
 */
test('按会话搜索：过滤、无匹配提示、清除后恢复', async ({ page, request }) => {
  const stamp = Date.now()
  const hit = `alpha${stamp}`
  const miss = `beta${stamp}`
  for (const name of [hit, miss]) {
    const created = await createSession(request, { name })
    expect(created.status(), `造会话失败：${await created.text()}`).toBe(201)
  }

  await page.goto(`${BASE}/sessions`)
  const nav = page.locator('section[aria-label="工作区"]')
  const hitRow = nav.getByRole('button', { name: new RegExp(`^${hit}`) })
  const missRow = nav.getByRole('button', { name: new RegExp(`^${miss}`) })
  await expect(hitRow).toBeVisible({ timeout: 10_000 })
  await expect(missRow).toBeVisible()

  // 打开搜索并输入：只剩匹配的那条，另一条连按钮都不存在。
  await page.getByRole('button', { name: '搜索会话' }).click()
  const box = page.getByRole('searchbox', { name: '搜索会话' })
  await box.fill(hit)
  await expect(hitRow).toBeVisible()
  await expect(missRow).toHaveCount(0)

  // 没有匹配：给"没有名字含 X 的会话"，而不是空列表。
  await box.fill(`zzz${stamp}`)
  await expect(nav.getByText(new RegExp(`没有名字含`))).toBeVisible()
  await expect(hitRow).toHaveCount(0)

  // 清除后恢复。
  await page.getByRole('button', { name: '清除搜索' }).click()
  await expect(hitRow).toBeVisible()
  await expect(missRow).toBeVisible()
})
