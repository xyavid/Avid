import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  attachmentUrl,
  createSession,
  createWorkspace,
  deleteSession,
  getMeta,
  listEntries,
  listSessions,
  pickFolder,
  renameSession,
} from '../client'

function jsonResponse(status: number, body: unknown): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

/** A fetch that never answers on its own and rejects the moment its signal is aborted. */
function hangingFetch() {
  return vi.fn(
    (_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () =>
          reject(new DOMException('aborted', 'AbortError')),
        )
      }),
  )
}

describe('api/client（网络出口唯一层）', () => {
  it('getMeta 解析 meta DTO', async () => {
    const meta = { api_version: 1, features: {}, event_types: [], capabilities: {}, stream: {} }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, meta))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getMeta()).resolves.toEqual(meta)
    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('/api/meta')
    // Every request goes out with a deadline, so the signal is always ours, not the caller's.
    expect((init as RequestInit).signal).toBeInstanceOf(AbortSignal)
  })

  it('listSessions 命中 /api/sessions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { sessions: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listSessions()).resolves.toEqual({ sessions: [] })
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions')
  })

  it('listEntries 拼查询参数：branch/order/limit', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { entries: [], has_more: false, next_cursor: null }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await listEntries('s1', { limit: 50 })
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/sessions/s1/entries?branch=main&order=desc&limit=50',
    )
  })

  it('非 2xx 且带约定信封：按信封抛 ApiError（code/message/status）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(404, { error: { code: 'not_found', message: '会话不存在' } }),
      ),
    )

    const err = await listSessions().catch((e: unknown) => e) as ApiError
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('not_found')
    expect(err.message).toBe('会话不存在')
    expect(err.status).toBe(404)
  })

  it('非 JSON 的非 2xx：探 /api/health 区分「后端不在这儿」与「后端答坏了」', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('<html>502</html>', { status: 502 }))
      .mockResolvedValueOnce(jsonResponse(200, { status: 'ok' })) // health probe succeeds
    vi.stubGlobal('fetch', fetchMock)

    const err = await getMeta().catch((e: unknown) => e) as ApiError
    expect(err.code).toBe('bad_gateway')
    expect(err.message).toContain('后端')
  })

  it('fetch 直接抛错（后端没起）：给可执行的下一步', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const err = await getMeta().catch((e: unknown) => e) as ApiError
    expect(err.message).toContain('avid web')
  })

  it('pickFolder 走 POST /api/workspaces/pick', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { path: '/tmp/x' }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(pickFolder()).resolves.toEqual({ path: '/tmp/x' })
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/workspaces/pick')
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST')
  })

  it('createWorkspace POST 带 JSON 载荷', async () => {
    const created = { id: 'w1', root: '/tmp/x', name: null, created_at: 0, last_used_at: 0, is_default: false }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, created))
    vi.stubGlobal('fetch', fetchMock)

    await expect(createWorkspace({ path: '/tmp/x' })).resolves.toEqual(created)
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ path: '/tmp/x' }))
  })

  it('createSession POST /api/sessions，载荷带 workspace（服务端把它当必填项）', async () => {
    const created = { id: 's9', name: null, branch: 'main' }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, created))
    vi.stubGlobal('fetch', fetchMock)

    await expect(createSession({ workspace: 'w1' })).resolves.toEqual(created)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions')
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ workspace: 'w1' }))
  })

  it('renameSession PATCH /api/sessions/{id}，载荷只有 name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: 's1', name: '新名字' }))
    vi.stubGlobal('fetch', fetchMock)

    await renameSession('s1', '新名字')
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions/s1')
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.method).toBe('PATCH')
    expect(init.body).toBe(JSON.stringify({ name: '新名字' }))
  })

  it('deleteSession DELETE /api/sessions/{id}：204 无正文，不解析 JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(204, null))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteSession('s 1')).resolves.toBeUndefined()
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions/s%201')
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe('DELETE')
  })

  it('attachmentUrl 也走后端地址：它会被放进 <img src>，相对路径会问到页面自己的源', () => {
    // Unset base: same shape as before, so a reverse-proxy deployment keeps working unchanged.
    expect(attachmentUrl('s 1', 'e/2', 0)).toBe('/api/sessions/s%201/entries/e%2F2/attachments/0')
  })
})

describe('api/client（截止时间与取消）', () => {
  it('卡住的普通读在 15 秒被判超时，且不冒充连接失败', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', hangingFetch())

    const pending = getMeta()
    const outcome = expect(pending).rejects.toMatchObject({ code: 'timeout' })
    await vi.advanceTimersByTimeAsync(15_000)
    await outcome
  })

  it('调用方取消抛 aborted：视图先走了，不是失败', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', hangingFetch())

    const controller = new AbortController()
    const pending = getMeta(controller.signal)
    const outcome = expect(pending).rejects.toMatchObject({ code: 'aborted' })
    controller.abort()
    await outcome
  })

  it('取消与截止时间谁先到谁说话', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', hangingFetch())

    // Deadline first: the request already failed for that reason, and a later abort cannot relabel
    // it. The component that aborted checks `signal.aborted` before touching state either way.
    const late = new AbortController()
    const firstOutcome = expect(getMeta(late.signal)).rejects.toMatchObject({ code: 'timeout' })
    await vi.advanceTimersByTimeAsync(15_000)
    late.abort()
    await firstOutcome

    // Caller first: nothing had failed yet, so the cancellation is what happened.
    const early = new AbortController()
    const secondOutcome = expect(getMeta(early.signal)).rejects.toMatchObject({ code: 'aborted' })
    early.abort()
    await vi.advanceTimersByTimeAsync(15_000)
    await secondOutcome
  })

  it('等外部的那类调用不吃本地截止时间：15 秒后仍活着，300 秒才收', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', hangingFetch())

    // pickFolder waits on a person in a native dialog; the local deadline would cut it short.
    const pending = pickFolder()
    let settled = false
    void pending.catch(() => {
      settled = true
    })
    const outcome = expect(pending).rejects.toMatchObject({ code: 'timeout' })
    await vi.advanceTimersByTimeAsync(15_000)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(285_000)
    await outcome
  })
})
