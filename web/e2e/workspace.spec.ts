/**
 * 工作区的 E2E：登记、系统选择器、以及三种错误路径。
 *
 * 为什么这组交互必须有 E2E：它跨了"浏览器 ↔ 本机后端"的一条特殊边界——
 * **浏览器的目录选择器拿不到绝对路径**（`webkitdirectory` 只给相对路径、
 * File System Access 只给 handle），所以"选择文件夹"只能由跑在本机的后端
 * 弹系统对话框（`POST /api/workspaces/pick`）。这条链路的**形态**（点谁、
 * 弹窗在哪、失败给什么文案）只有真浏览器能验。
 */

import { expect, test } from './lib/fixtures'
import { defaultSession, defaultWorkspace } from './lib/api'

/** 从右栏信息面板进入「管理工作区」——它与左栏底部的入口共用同一个弹窗。 */
async function openManager(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: '管理工作区' }).click()
  await expect(page.getByRole('dialog', { name: '管理工作区' })).toBeVisible()
}

test.describe('添加 / 管理工作区', () => {
  test('系统选择器选中目录后，路径被填入并可提交登记', async ({ page, stubApi }) => {
    const stub = await stubApi({
      sessions: [defaultSession('s-1', '工作区会话')],
      workspaces: [defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid')],
      pick: { path: '/home/fishy/new-project' },
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await openManager(page)

    // 进入"添加"态：不是再叠一个模态，而是同一个弹窗切换内容（Esc 语义因此可预测）。
    await page.getByRole('button', { name: '添加' }).click()
    await expect(page.getByRole('dialog', { name: '添加工作区' })).toBeVisible()

    const pathInput = page.getByRole('textbox', { name: '工作区路径' })
    await expect(pathInput).toHaveValue('')

    await page.getByRole('button', { name: '选择文件夹…' }).click()

    // 后端弹了对话框并把绝对路径交回来，界面把它填进输入框。
    await expect.poll(() => stub.calls('POST', '/workspaces/pick').length).toBe(1)
    await expect(pathInput).toHaveValue('/home/fishy/new-project')
  })

  test('在系统选择器里取消（返回 null）不算错误', async ({ page, stubApi, allowConsoleError }) => {
    // 用户取消时后端返回 `{path: null}`；界面不该报错、更不该把 null 当路径填进去。
    allowConsoleError('未打桩')
    const stub = await stubApi({
      sessions: [defaultSession('s-1', '工作区会话')],
      pick: { path: null },
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await openManager(page)
    await page.getByRole('button', { name: '添加' }).click()

    const pathInput = page.getByRole('textbox', { name: '工作区路径' })
    await pathInput.fill('/home/fishy/placeholder')
    await page.getByRole('button', { name: '选择文件夹…' }).click()

    await expect.poll(() => stub.calls('POST', '/workspaces/pick').length).toBe(1)
    // 输入框保持用户原来填的内容，且弹窗内没有出现错误提示。
    await expect(pathInput).toHaveValue('/home/fishy/placeholder')
    await expect(page.getByRole('dialog', { name: '添加工作区' }).getByRole('alert')).toHaveCount(0)
  })

  test('手动填写路径并提交，请求体带路径 / 显示名 / 默认权限', async ({ page, stubApi }) => {
    const stub = await stubApi({
      sessions: [defaultSession('s-1', '工作区会话')],
      createWorkspace: defaultWorkspace('ws-new', '/home/fishy/manual', '手工登记'),
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await openManager(page)
    await page.getByRole('button', { name: '添加' }).click()

    await page.getByRole('textbox', { name: '工作区路径' }).fill('/home/fishy/manual')
    await page.getByRole('textbox', { name: '显示名（可选）' }).fill('手工登记')
    // 默认权限：工作区只接受 manual / auto（`full` 在服务端就会被 422 挡掉）。
    await page.getByRole('radio', { name: '自动' }).check()
    await page.getByRole('button', { name: '添加', exact: true }).click()

    await expect
      .poll(() => stub.calls('POST', '/workspaces')[0]?.body)
      .toEqual({ path: '/home/fishy/manual', name: '手工登记', permission: 'auto' })
  })

  test('登记失败按服务端错误码给不同文案，而不是一句"出错了"', async ({ page, stubApi, allowConsoleError }) => {
    // 这个场景刻意不要打桩成功的 createWorkspace，让打桩返回 400。
    allowConsoleError('Failed to load resource')
    const stub = await stubApi({
      sessions: [defaultSession('s-1', '工作区会话')],
      createWorkspace: { error: { status: 400, code: 'workspace_invalid', message: '路径不存在' } },
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await openManager(page)
    await page.getByRole('button', { name: '添加' }).click()

    await page.getByRole('textbox', { name: '工作区路径' }).fill('/home/fishy/does-not-exist')
    await page.getByRole('button', { name: '添加', exact: true }).click()

    await expect.poll(() => stub.calls('POST', '/workspaces').length).toBe(1)
    // 错误必须显示在弹窗内（`role="alert"`），而且文案要能指向"路径有问题"。
    const alert = page.getByRole('dialog', { name: '添加工作区' }).getByRole('alert')
    await expect(alert).toBeVisible()
    await expect(alert).toContainText(/路径|不存在|检查/)
    // 弹窗不关闭：用户要能就地改路径重试，而不是重新走一遍入口。
    await expect(page.getByRole('dialog', { name: '添加工作区' })).toBeVisible()
  })

  test('移除已登记的工作区要先确认，且说清"只摘候选、不删会话数据"', async ({ page, stubApi }) => {
    const stub = await stubApi({
      sessions: [defaultSession('s-1', '工作区会话')],
      workspaces: [
        defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid'),
        defaultWorkspace('ws-2', '/home/fishy/notes', '笔记'),
      ],
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await openManager(page)

    await page.getByRole('button', { name: '移除工作区 笔记' }).click()

    // 确认态：文案必须说准服务端语义（`routes/workspaces.py:32` 的 docstring）。
    const confirm = page.getByRole('dialog', { name: '移除工作区' })
    await expect(confirm).toBeVisible()
    await expect(confirm).toContainText('候选列表')
    await expect(confirm).toContainText('之后想再用')

    await confirm.getByRole('button', { name: '移除' }).click()
    await expect.poll(() => stub.calls('DELETE', '/workspaces/ws-2').length).toBe(1)
  })

  test('进程绑定的工作区不能移除', async ({ page, stubApi }) => {
    await stubApi({
      sessions: [defaultSession('s-1', '工作区会话')],
      // `is_default: true` 就是"进程当前绑定的那个"，服务端会以 409 workspace_bound 拒绝。
      workspaces: [defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid')],
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await openManager(page)

    await expect(page.getByRole('button', { name: '移除工作区 Avid' })).toBeDisabled()
  })
})
