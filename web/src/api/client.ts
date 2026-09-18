/**
 * 唯一网络出口（L0 之下不存在 fetch）。
 *
 * 三条纪律：
 *   · 每个请求都必须有**超时**与 **AbortSignal**（C11 的门禁会检查）；
 *   · 非 2xx 一律解析成 `ApiError`，code 来自服务端的稳定错误码（§6.2）；
 *   · 错误绝不静默：`catch` 块要么处理、要么显式标注 noop。
 */

import type { ErrorEnvelope } from './types'

export const API_BASE = '/api'
export const DEFAULT_TIMEOUT_MS = 15_000
/** 起运行要等内核开线程，给长一点的预算；模型调用本身不在这里等。 */
export const MUTATION_TIMEOUT_MS = 60_000

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: Record<string, unknown>

  constructor(
    status: number,
    code: string,
    message: string,
    detail: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

function toApiError(status: number, payload: unknown): ApiError {
  const envelope = payload as Partial<ErrorEnvelope> | null
  const body = envelope?.error
  if (body && typeof body.code === 'string') {
    return new ApiError(status, body.code, body.message, body.detail ?? {})
  }
  return new ApiError(status, 'unexpected_response', `HTTP ${status}`)
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = 'GET',
    body,
    signal,
    timeoutMs = method === 'GET' ? DEFAULT_TIMEOUT_MS : MUTATION_TIMEOUT_MS,
  } = options

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort(new Error('timeout'))
  }, timeoutMs)
  const forward = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', forward)

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })

    if (response.status === 204) {
      return undefined as T
    }

    const text = await response.text()
    let payload: unknown = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        throw new ApiError(response.status, 'bad_json', `响应不是合法 JSON：${path}`)
      }
    }

    if (!response.ok) {
      throw toApiError(response.status, payload)
    }
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    const reason = (error as Error)?.message ?? String(error)
    const aborted = (error as Error)?.name === 'AbortError' || timedOut
    throw new ApiError(
      0,
      timedOut ? 'timeout' : aborted ? 'aborted' : 'network_error',
      reason,
    )
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', forward)
  }
}
