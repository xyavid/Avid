// @vitest-environment jsdom
/**
 * `request` 的错误分支。
 *
 * 这组用例的由来是一次真实报障：`pnpm dev` 起在前端、后端忘了起，界面显示
 * **`HTTP 500（unexpected_response）`**。它把人引向"服务端内部错误"，
 * 而真实原因是"后端不在那个端口上"——两者该做的事完全不同（一个去查堆栈，
 * 一个去起进程 / 对端口）。
 *
 * 所以这里钉住的不是"会不会报错"，而是**报的错能不能指到真正的原因**。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request } from '../client'

/** 造一条 fetch Response。`body` 给字符串就是原样正文（用来模拟非 JSON 响应）。 */
function response(status: number, body: string, contentType = 'text/plain'): Response {
  return new Response(body, { status, headers: { 'content-type': contentType } })
}

const json = (status: number, payload: unknown) =>
  response(status, JSON.stringify(payload), 'application/json')

describe('request 的错误分支', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('把"代理连不上后端"识别成 backend_unreachable，而不是 HTTP 500', async () => {
    /*
     * 复刻真实现场：Vite 代理在目标端口无人监听时回一条
     * `500` + `text/plain` + **空体**，而 `/api/health` 探测同样失败。
     */
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/health')) return Promise.reject(new TypeError('Failed to fetch'))
        return Promise.resolve(response(500, ''))
      }),
    )

    const error = await request('/meta').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('backend_unreachable')
    // 消息必须给出下一步动作，而不是只报一个状态码。
    expect((error as ApiError).message).toContain('连不上本地服务')
    expect((error as ApiError).message).toContain('8765')
  })

  it('后端活着但某个响应坏掉时，仍报 bad_json（不误判成"后端没起"）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        // 就绪探针成功 → 后端是活着的，本次失败另有原因。
        if (url.includes('/health')) return Promise.resolve(json(200, { status: 'ok' }))
        return Promise.resolve(response(500, '<html>反代挂了</html>', 'text/html'))
      }),
    )

    const error = await request('/meta').catch((caught: unknown) => caught)

    expect((error as ApiError).code).toBe('bad_json')
    expect((error as ApiError).message).toContain('不是合法 JSON')
  })

  it('服务端的错误信封被还原成稳定错误码与原文', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          json(409, { error: { code: 'workspace_exists', message: '已经登记过', detail: { id: 'w-1' } } }),
        ),
      ),
    )

    const error = await request('/workspaces', { method: 'POST', body: {} }).catch(
      (caught: unknown) => caught,
    )

    expect((error as ApiError).code).toBe('workspace_exists')
    expect((error as ApiError).message).toBe('已经登记过')
    expect((error as ApiError).detail).toEqual({ id: 'w-1' })
  })

  it('正常路径不会去探测 /health（诊断只在错误路径上发生）', async () => {
    // 显式声明成接收一个 URL 参数的函数：否则 `mock.calls[0][0]` 在类型上越界
    // （零参函数的 calls 元组是空元组），正是 tsc 抓到的那个问题。
    const fetchMock = vi.fn((_input: RequestInfo | URL) => Promise.resolve(json(200, { ok: true })))
    vi.stubGlobal('fetch', fetchMock)

    await request('/meta')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0]![0])).not.toContain('/health')
  })
})
