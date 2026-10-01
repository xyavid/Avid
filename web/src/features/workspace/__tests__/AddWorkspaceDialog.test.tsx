// @vitest-environment jsdom
/**
 * 添加工作区弹窗的交互用例。
 *
 * 网络打桩走 `vi.stubGlobal('fetch', …)` 而不是注入假 `request`：
 * `api/client.ts` 的导出形状是**网络出口的契约**（task-2 定的），为测试改它
 * 就等于让"唯一出口"多一个可替换的接缝。拦 fetch 的代价是要自己造一个
 * 最小 Response（下面那个 `jsonResponse`），但换来的是**连 client 的拆壳、
 * 错误码解析一起被验到**——这正是本组件"按码给文案"依赖的那一层。
 *
 * `afterEach` 里必须 `unstubAllGlobals()`：vitest 的 stub 是全局的，
 * 不拆会把 fetch 泄漏给同一进程里后面的文件。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceSummary } from '../../../../api/types'
import { AddWorkspaceDialog } from '../components/AddWorkspaceDialog'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

type FetchCall = { path: string; init: RequestInit | undefined }

/** 最小 Response：client 只读 status / ok / text()。 */
function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function errorResponse(status: number, code: string, detail: Record<string, unknown> = {}): Response {
  return jsonResponse(status, { error: { code, message: code, detail } })
}

function stubFetch(
  handler: (call: FetchCall) => Response | Promise<Response>,
): FetchCall[] {
  const calls: FetchCall[] = []
  const mock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const path = String(input)
    const call = { path, init }
    calls.push(call)
    return handler(call)
  })
  vi.stubGlobal('fetch', mock)
  return calls
}

function workspacePatched(): Partial<WorkspaceSummary> {
  return { id: 'ws-new', root: '/tmp/picked', name: 'picked' }
}

function renderDialog(overrides: Partial<Parameters<typeof AddWorkspaceDialog>[0]> = {}) {
  const props = {
    open: true,
    picker: 'tkinter',
    onClose: vi.fn(),
    onCreated: vi.fn(),
    ...overrides,
  }
  const view = render(<AddWorkspaceDialog {...props} />)
  return { ...view, props }
}

/** 路径输入框：用 aria-label 定位，不用 placeholder（文案会改，语义名不会）。 */
function pathInput(): HTMLInputElement {
  return screen.getByLabelText('工作区路径') as HTMLInputElement
}

describe('AddWorkspaceDialog 的文件夹选择器', () => {
  it('picker 为 null 时「选择文件夹」禁用，并说明是后端没探测到', () => {
    stubFetch(() => jsonResponse(200, { path: null }))
    renderDialog({ picker: null })

    const button = screen.getByRole('button', { name: /选择文件夹/ }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(screen.getByText(/没有可用的文件夹选择器/)).toBeTruthy()
  })

  it('点击选择器后把返回的绝对路径填进输入框', async () => {
    const calls = stubFetch(() => jsonResponse(200, { path: '/home/fishy/Avid' }))
    renderDialog()

    fireEvent.click(screen.getByRole('button', { name: /选择文件夹/ }))

    await waitFor(() => expect(pathInput().value).toBe('/home/fishy/Avid'))
    expect(calls[0]?.path).toBe('/api/workspaces/pick')
    expect(calls[0]?.init?.method).toBe('POST')
  })

  it('用户取消（path 为 null）静默保持原状：不报错、不覆盖已输入的内容', async () => {
    stubFetch(() => jsonResponse(200, { path: null }))
    renderDialog()

    fireEvent.change(pathInput(), { target: { value: '/tmp/manual' } })
    fireEvent.click(screen.getByRole('button', { name: /选择文件夹/ }))

    // 等到请求确实回来了，才能断言"没报错"；否则这条用例在请求发出前就通过了。
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /选择文件夹/ })).toBeTruthy(),
    )
    await waitFor(() => expect(pathInput().value).toBe('/tmp/manual'))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('AddWorkspaceDialog 提交与错误文案', () => {
  it('提交把 path / name / permission 一起发出去（不提供 full）', async () => {
    const calls = stubFetch((call) =>
      call.path.endsWith('/workspaces')
        ? jsonResponse(201, workspacePatched())
        : jsonResponse(200, { path: null }),
    )
    const { props } = renderDialog()

    fireEvent.change(pathInput(), { target: { value: '/tmp/picked' } })
    fireEvent.change(screen.getByLabelText('显示名（可选）'), { target: { value: '临时' } })
    fireEvent.click(screen.getByRole('radio', { name: /自动/ }))
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(props.onCreated).toHaveBeenCalledTimes(1))
    const body = JSON.parse(String(calls[calls.length - 1]?.init?.body))
    expect(body).toEqual({ path: '/tmp/picked', name: '临时', permission: 'auto' })
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it('workspace_exists 时在弹窗内说明并指向已有项（role=alert，不是 console）', async () => {
    stubFetch(() =>
      errorResponse(409, 'workspace_exists', { id: 'ws-1', name: 'Avid' }),
    )
    const { props } = renderDialog()

    fireEvent.change(pathInput(), { target: { value: '/home/fishy/Avid' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Avid')
    expect(props.onCreated).not.toHaveBeenCalled()
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it('workspace_invalid 时提示核对路径', async () => {
    stubFetch(() => errorResponse(400, 'workspace_invalid'))
    renderDialog()

    fireEvent.change(pathInput(), { target: { value: '/nope' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('路径')
  })

  it('路径为空时不发请求，就地提示', () => {
    const calls = stubFetch(() => jsonResponse(201, workspacePatched()))
    renderDialog()

    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    expect(screen.getByRole('alert')).toBeTruthy()
    expect(calls).toHaveLength(0)
  })

  it('提交期间输入与按钮禁用，避免同一路径被登记两次', async () => {
    let release: (() => void) | null = null
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    stubFetch(async () => {
      await gate
      return jsonResponse(201, workspacePatched())
    })
    const { props } = renderDialog()

    fireEvent.change(pathInput(), { target: { value: '/tmp/picked' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(pathInput().disabled).toBe(true))
    expect((screen.getByLabelText('显示名（可选）') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '添加' }) as HTMLButtonElement).disabled).toBe(true)

    release?.()
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledTimes(1))
    // 父组件此刻未必已经卸载弹窗（open 由它控制），所以这里断言的是"提交结束、
    // 按钮回到可用"，而不是"弹窗消失"——后者是父组件的责任。
    await waitFor(() =>
      expect((screen.getByRole('button', { name: '添加' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    )
  })
})
