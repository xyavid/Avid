import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
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
})

describe('api/client（网络出口唯一层）', () => {
  it('getMeta 解析 meta DTO', async () => {
    const meta = { api_version: 1, features: {}, event_types: [], capabilities: {}, stream: {}, build: {} }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, meta))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getMeta()).resolves.toEqual(meta)
    expect(fetchMock).toHaveBeenCalledWith('/api/meta', undefined)
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
})
