/**
 * 唯一网络出口：`web/src/` 里除了本文件与 `stream.ts` 的 SSE 取流，不得出现 `fetch`。
 *
 * 三条纪律，缺一条都会在真实网络条件下出问题：
 *   1. **每个请求都有超时与 `AbortSignal`**。没有超时，一次挂住的服务端调用会让
 *      "加载中"永远转下去；没有外部 `signal`，切会话后旧请求仍会回来覆盖新状态。
 *   2. **非 2xx 一律解析成 `ApiError`**，`code` 取服务端的稳定错误码
 *      （`{"error": {"code": ..., "message": ..., "detail": ...}}`，见 `schemas.py`
 *      的 `ErrorOut`）。界面按 `code` 分支，不按文案分支。
 *   3. **错误绝不静默**：`catch` 里要么转成 `ApiError` 抛出去，要么显式说明为什么吞。
 *      这里没有第二种情况——所有失败都抛。
 */

import type { ErrorEnvelope } from './types'

export const API_BASE = '/api'
/** 读操作的预算：本地服务端读一次会话文件，15s 已经非常宽裕。 */
export const DEFAULT_TIMEOUT_MS = 15_000
/** 写操作的预算：起运行要等内核开线程、建工作区要落盘，给长一点的预算。 */
export const MUTATION_TIMEOUT_MS = 60_000

export class ApiError extends Error {
  /** HTTP 状态码；`0` 表示请求根本没拿到响应（超时 / 被取消 / 网络失败）。 */
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

/**
 * 后端到底在不在监听？只用于**错误路径**上的诊断，不参与正常请求。
 *
 * 判据是"能不能拿到一条 2xx"——`/api/health` 是就绪探针，不需要会话也不需要鉴权。
 * 任何异常（网络失败、超时、非 2xx）都算"不在"，因为我们要区分的是
 * "后端活着但那个请求坏了"与"后端压根不在这儿"。
 */
async function backendAlive(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      // 短超时：这是诊断路径，不该让错误提示再等 15 秒才出现。
      signal: AbortSignal.timeout(2_000),
    })
    return response.ok
  } catch {
    return false
  }
}

/**
 * 服务端的错误信封 → `ApiError`；信封不成立时给一个稳定的兜底码。
 *
 * `backendDown` 由调用方在**响应形状不合约定时**探测后传进来，用来把
 * "后端不在那个端口"与"后端这次答坏了"分开——两者的下一步动作完全不同。
 */
function toApiError(status: number, payload: unknown, backendDown = false): ApiError {
  const envelope = payload as Partial<ErrorEnvelope> | null
  const body = envelope?.error
  if (body && typeof body.code === 'string') {
    return new ApiError(status, body.code, body.message, body.detail ?? {})
  }
  /*
   * 形状不合约定的错误里，**最容易误诊的一类**是开发代理连不上后端：
   * Vite 的代理在目标端口无人监听时回 `500` + 空体 / text-plain，
   * 若原样报成 `HTTP 500`，用户会以为服务端内部出错（去翻堆栈），
   * 而真实原因是"后端没起在这儿"（该去起进程或对端口）。
   */
  if (backendDown) {
    return new ApiError(
      status,
      'backend_unreachable',
      `连不上本地服务：响应不是约定的 JSON 信封（HTTP ${status}）。` +
        '开发期请确认后端已起、且端口与 Vite 代理目标一致（默认 127.0.0.1:8765）。',
    )
  }
  // 未知路径、反代返回的 HTML 等：形状不合约定的错误也要有码可判。
  return new ApiError(status, 'unexpected_response', `HTTP ${status}`)
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = 'GET',
    body,
    signal,
    timeoutMs = method === 'GET' ? DEFAULT_TIMEOUT_MS : MUTATION_TIMEOUT_MS,
  } = options

  // 已经取消的调用直接拒绝：否则 `addEventListener('abort')` 不会再触发，请求会照发。
  if (signal?.aborted) {
    throw new ApiError(0, 'aborted', `请求已取消：${path}`)
  }

  // 自己的 controller 把"超时"与"外部取消"合并成一条取消路径，`fetch` 只认一个 signal。
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const forward = () => controller.abort()
  signal?.addEventListener('abort', forward)

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })

    // 204 没有正文：`undefined` 是"成功且没有载荷"，与"载荷是 null"区分开。
    if (response.status === 204) {
      return undefined as T
    }

    const text = await response.text()
    let payload: unknown = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        /*
         * 非 JSON 的非 2xx：**先认出"开发代理连不上后端"这一种**，再谈坏 JSON。
         *
         * 这个分支是实际报障换来的：`pnpm dev` 的 Vite 代理把 /api 转到 127.0.0.1:8765，
         * 如果后端没起在**那个**端口，代理会回一条 `500` + `text/plain` + **空体**。
         * 原来的实现把它报成 `HTTP 500（unexpected_response）`，用户看到的是
         * "服务端内部错误"——一个把人引向错误方向的消息：真正的事实是"后端不在这儿"，
         * 与界面、与内核逻辑都无关。
         *
         * 这里不猜具体原因，只做一个**一次性的探测**：另外请求一次 `/api/health`
         * （该端点不需要鉴权、不需要会话、永远 200），用来区分两种截然不同的处境：
         *   · 探测失败 → 后端根本没在监听：给出"先起后端 / 端口要对上"这类可执行提示；
         *   · 探测成功 → 后端活着，是**这一个**请求或它前面的中间层出了问题，如实转述。
         * 之所以只在错误路径上探测：正常路径一个字节的额外开销都不该有。
         */
        if (!(await backendAlive())) {
          throw toApiError(response.status, null, true)
        }
        throw new ApiError(
          response.status,
          'bad_json',
          `响应不是合法 JSON（HTTP ${response.status}）：${path}`,
        )
      }
    }

    if (!response.ok) {
      /*
       * 空体 / 非 JSON 的错误响应形状不合约定时才去探测（见上）。
       * 已经解出信封的走原路，不额外发请求。
       */
      const knownEnvelope = (payload as Partial<ErrorEnvelope> | null)?.error
      if (knownEnvelope || payload !== null) {
        throw toApiError(response.status, payload)
      }
      throw toApiError(response.status, payload, !(await backendAlive()))
    }
    // 空 body 的 2xx 回到 `null`（不是 `undefined`）：调用方按"没有数据"处理。
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    // 剩下的三种：自己的超时、外部取消、真正的网络失败。它们必须给出不同的码，
    // 否则界面无法区分"该重试"与"用户自己取消的"。
    const reason = error instanceof Error ? error.message : String(error)
    const aborted = error instanceof Error && error.name === 'AbortError'
    const code = timedOut ? 'timeout' : aborted || signal?.aborted ? 'aborted' : 'network_error'
    throw new ApiError(0, code, `请求失败（${code}）：${path} — ${reason}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', forward)
  }
}
