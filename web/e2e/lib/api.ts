/**
 * `/api/**` 的请求打桩（E2E 的唯一后端替身）。
 *
 * 为什么用 `page.route` 而不是真起一个后端 + 真起 mock 服务：
 *   · 本次 E2E 验的是**界面**（布局、交互、错误态、SSE 增量），不是线格式；
 *     线格式由 `tests/test_wire_contract.py` 与 `tests/test_event_contract.py` 逐字段守。
 *   · 在浏览器层拦请求还能造出真后端很难造的场景：500、超时、SSE 中途断开、
 *     审批 409 已决——这些恰恰是界面最容易写错的地方。
 *
 * 边界要说清：它**不**证明前后端能跑通。真实联调仍需 `uv run avid web` + `pnpm dev`。
 */

import type { Page, Route } from '@playwright/test'

import type { Meta, SessionSummary, WorkspaceSummary } from '../../src/api/types'

/** 场景补丁：只覆盖本用例关心的端点，其余用默认值。 */
export interface ApiScenario {
  meta?: Meta
  sessions?: SessionSummary[]
  workspaces?: WorkspaceSummary[]
  /** 会话详情（`GET /api/sessions/{id}`）。 */
  session?: Partial<SessionSummary> & { branch?: string }
  branches?: unknown
  entries?: unknown
  /** 起运行的响应；给 `error` 则返回对应错误。 */
  startRun?: { run_id?: string } | { error: { status: number; code: string; message: string } }
  /** SSE 帧序列；空数组 = 连接后不发事件（用来测"连上了但没有输出"）。 */
  events?: unknown[]
  approvals?: unknown[]
  /** 应答审批的结果；给 `error` 则返回错误。 */
  answerApproval?: { decision?: string } | { error: { status: number; code: string; message: string } }
  /** `POST /api/workspaces/pick` 的返回；`{ path: null }` = 用户取消。 */
  pick?: { path: string | null } | { error: { status: number; code: string; message: string } }
  /** `POST /api/workspaces` 的返回；给 `error` 则返回错误。 */
  createWorkspace?: unknown
  /** 让所有请求延迟这么多毫秒（测加载态）。 */
  delayMs?: number
}

export interface RouteLogEntry {
  method: string
  path: string
  body: unknown
}

export interface ApiStub {
  /** 按顺序记录命中的请求，用例据此断言"发了什么"。 */
  log: RouteLogEntry[]
  /** 只取某个方法的记录。 */
  calls(method: string, pathIncludes?: string): RouteLogEntry[]
}

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({
    status,
    contentType: 'application/json',
    // 信任边界中间件会比 Host/Origin，这里直接给合法值；它挡的是 DNS rebinding，
    // 与 E2E 要验的界面无关，不该成为打桩的噪声来源。
    body: JSON.stringify(body),
  })

const errorBody = (code: string, message: string) => ({
  error: { code, message, detail: {} },
})

/** 编一条 SSE 帧。事件 `data` 用 JSON，与后端 `EventPayload` 同形。 */
export function sseFrame(payload: unknown, id?: number): string {
  const lines = [`event: message`]
  if (id !== undefined) lines.push(`id: ${id}`)
  lines.push(`data: ${JSON.stringify(payload)}`)
  return `${lines.join('\n')}\n\n`
}

export async function installApiStubs(page: Page, scenario: ApiScenario = {}): Promise<ApiStub> {
  const log: RouteLogEntry[] = []

  const meta: Meta = scenario.meta ?? defaultMeta()
  const sessions = scenario.sessions ?? [defaultSession('s-1', '一个新的会话')]
  const workspaces = scenario.workspaces ?? [defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid')]
  const entries = scenario.entries ?? { entries: [], has_more: false, next_cursor: null, truncated_tail: false }

  /*
   * favicon：`index.html` 用的是 `data:,` 空图标，但 Chromium 仍可能去要 `/favicon.ico`。
   * 直接给个 204，避免它在控制台留一条噪声——E2E 里"每条用例自动断言控制台干净"，
   * 噪声源必须在基座里清掉，而不是让每个 spec 各自豁免。
   */
  await page.route(/\/favicon\.ico$/, (route) => route.fulfill({ status: 204, body: '' }))

  /*
   * 匹配规则：**`/api/` 必须紧跟在 origin 之后**。
   *
   * 踩过的坑（值得记下来，因为它表现为"整个应用白屏"：
   *   1. `**​/api/**` 通配：Vite 开发态的源码模块 `/src/api/types.ts`、`/src/api/client.ts`
   *      也含 `/api/`，被拦下并返回 JSON → 浏览器报
   *      "Expected a JavaScript-or-Wasm module script but the server responded with
   *      a MIME type of application/json" → **白屏**，而失败信息只说"找不到 h1"。
   *   2. 正则 `/\/api\//` 同样中招：它匹配**整个 URL 字符串**，`/src/api/…` 也命中。
   *   3. `page.route` 的谓词函数**只收到 `(url)`**（不是 `(url, request)`），
   *      所以拿不到 `resourceType()` 来排除 module script——这条路走不通。
   *
   * 结论：用锚定 origin 的正则。`^https?://<host>/api/` 只匹配挂在站点根下的
   * `/api/*`，而 Vite 的 `/src/api/*`、`/node_modules/.vite/deps/@api*` 都不满足。
   */
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
      const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()
    const raw = request.postData()
    let body: unknown = null
    if (raw !== null) {
      try {
        body = JSON.parse(raw)
      } catch {
        body = raw
      }
    }
    log.push({ method, path, body })

    if (scenario.delayMs !== undefined) {
      // 用来测加载态：把响应推到下一帧之后。
      await new Promise((resolve) => setTimeout(resolve, scenario.delayMs))
    }

    // ---- 元信息与列表 ----
    if (path === '/meta' && method === 'GET') return json(route, 200, meta)
    if (path === '/health') return json(route, 200, { status: 'ok', api_version: 1, uptime_ms: 1 })

    if (path === '/sessions' && method === 'GET') return json(route, 200, { sessions })
    if (path === '/sessions' && method === 'POST') {
      const created = defaultSession('s-new', '新会话')
      sessions.unshift(created)
      return json(route, 201, { ...created, branch: 'main' })
    }
    if (path === '/workspaces' && method === 'GET') return json(route, 200, { workspaces })

    if (path === '/workspaces' && method === 'POST') {
      if (scenario.createWorkspace !== undefined) {
        const patch = scenario.createWorkspace as { error?: { status: number; code: string; message: string } }
        if (patch.error) return json(route, patch.error.status, errorBody(patch.error.code, patch.error.message))
        workspaces.push(patch as WorkspaceSummary)
        return json(route, 201, patch)
      }
      const created = defaultWorkspace('ws-new', '/tmp/new-workspace', 'new-workspace')
      workspaces.push(created)
      return json(route, 201, created)
    }

    if (path === '/workspaces/pick' && method === 'POST') {
      const pick = scenario.pick ?? { path: '/tmp/picked-workspace' }
      const patch = pick as { error?: { status: number; code: string; message: string } }
      if (patch.error) return json(route, patch.error.status, errorBody(patch.error.code, patch.error.message))
      return json(route, 200, pick)
    }

    const workspaceDelete = /^\/workspaces\/([^/]+)$/.exec(path)
    if (workspaceDelete && method === 'DELETE') {
      const id = workspaceDelete[1]
      const index = workspaces.findIndex((item) => item.id === id)
      if (index >= 0) workspaces.splice(index, 1)
      return route.fulfill({ status: 204, body: '' })
    }

    // ---- 单个会话 ----
    const sessionMatch = /^\/sessions\/([^/]+)$/.exec(path)
    if (sessionMatch && method === 'GET') {
      const found = sessions.find((item) => item.id === sessionMatch[1]) ?? sessions[0]
      return json(route, 200, { ...found, ...(scenario.session ?? {}), branch: 'main' })
    }
    if (sessionMatch && method === 'PATCH') {
      const name = (body as { name?: string } | null)?.name ?? '改名'
      const index = sessions.findIndex((item) => item.id === sessionMatch[1])
      if (index >= 0) sessions[index] = { ...sessions[index]!, name }
      return json(route, 200, { ...sessions[index], branch: 'main' })
    }
    if (sessionMatch && method === 'DELETE') {
      const index = sessions.findIndex((item) => item.id === sessionMatch[1])
      // 有活动 run 时服务端会 409；E2E 正好可以验这条错误路径被界面正确显示。
      if (index >= 0 && sessions[index]!.active_run_id !== null) {
        return json(route, 409, errorBody('session_busy', '会话有活动运行，无法删除'))
      }
      if (index >= 0) sessions.splice(index, 1)
      return route.fulfill({ status: 204, body: '' })
    }

    if (/^\/sessions\/[^/]+\/branches$/.test(path) && method === 'GET') {
      return json(route, 200, scenario.branches ?? defaultBranches())
    }
    if (/^\/sessions\/[^/]+\/entries$/.test(path) && method === 'GET') {
      return json(route, 200, { session_id: sessions[0]?.id ?? 's-1', branch: 'main', order: 'asc', limit: 500, ...(entries as object) })
    }
    // 开分支（不是本次界面范围，但保持端点不 404 以免噪声）
    if (/^\/sessions\/[^/]+\/branches$/.test(path) && method === 'POST') {
      return json(route, 201, { name: 'b2', tip_entry_id: null, entry_count: 0, is_default: false, usage: null })
    }

    // ---- 运行 ----
    if (/^\/sessions\/[^/]+\/runs$/.test(path) && method === 'POST') {
      const startRun = scenario.startRun ?? { run_id: 'run-1' }
      const patch = startRun as { error?: { status: number; code: string; message: string } }
      if (patch.error) return json(route, patch.error.status, errorBody(patch.error.code, patch.error.message))
      return json(route, 201, { run_id: (startRun as { run_id?: string }).run_id ?? 'run-1', session_id: sessions[0]?.id ?? 's-1', status: 'running' })
    }

    if (/^\/runs\/[^/]+\/events$/.test(path)) {
      /*
       * SSE：把 scenario.events 逐帧写出，然后**保持连接打开**（不 end），
       * 直到页面主动断开。真后端也是这个行为——流不会因为"发完了"就关，
       * 它等下一次运行或心跳。若这里 end，用例会看到一次重连，噪声很大。
       */
      const frames = (scenario.events ?? []).map((event, index) => sseFrame(event, index + 100)).join('')
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-store' },
        body: frames,
      })
    }

    if (/^\/runs\/[^/]+\/approvals$/.test(path) && method === 'GET') {
      return json(route, 200, { approvals: scenario.approvals ?? [] })
    }

    if (/^\/runs\/[^/]+\/approvals\/[^/]+$/.test(path) && method === 'POST') {
      const answer = scenario.answerApproval ?? { decision: 'allow' }
      const patch = answer as { error?: { status: number; code: string; message: string } }
      if (patch.error) return json(route, patch.error.status, errorBody(patch.error.code, patch.error.message))
      return json(route, 200, {
        accepted: true,
        decision: (body as { decision?: string } | null)?.decision ?? 'allow',
        already: null,
        reason: '',
        approval_id: path.split('/').pop(),
      })
    }

    if (/^\/runs\/[^/]+$/.test(path) && method === 'GET') {
      return json(route, 200, {
        run_id: path.split('/').pop(),
        session_id: sessions[0]?.id ?? 's-1',
        status: 'running',
        started_at: Date.now(),
        finished_at: null,
        round: 1,
        tokens: 0,
        usage: null,
        error: null,
        cancel_requested: false,
        cancel_reason: null,
        pending_approvals: scenario.approvals ?? [],
      })
    }

    if (/^\/runs\/[^/]+\/cancel$/.test(path) && method === 'POST') {
      return json(route, 202, { run_id: path.split('/')[2], status: 'running', cancel_requested: true })
    }

    // 未打桩的端点：返回一个**指明路径**的 404，而不是静默成功。
    // 静默成功会让"界面调了不该调的端点"这类问题永远查不出来。
    return json(route, 404, errorBody('not_found', `E2E 未打桩：${method} ${path}`))
  })

  return {
    log,
    calls: (method, pathIncludes) =>
      log.filter((entry) => entry.method === method && (pathIncludes === undefined || entry.path.includes(pathIncludes))),
  }
}

// ---------------------------------------------------------------------------
// 默认数据（用例只覆盖自己关心的字段）
// ---------------------------------------------------------------------------

export function defaultMeta(): Meta {
  return {
    api_version: 1,
    features: { deltas: 1, branches: 1, usage: 1, workspace_picker: 1, workspace_delete: 1 },
    event_types: [],
    capabilities: {
      tools: ['read_file', 'shell', 'edit_file'],
      skills: [],
      model: 'deepseek/deepseek-v4.1-flash',
      workspace: '/home/fishy/Avid',
      workspace_picker: 'tkinter',
      sandbox: { backend: 'bwrap', available: true, network: true, reason: null, landlock_abi: 4 },
    },
    stream: { heartbeat_seconds: 15, terminal_fallback_seconds: 30, replay_buffer_size: 256 },
    build: { git_sha: 'e2e', built_at: null, source: 'e2e' },
  }
}

export function defaultSession(id: string, name: string | null): SessionSummary {
  return {
    id,
    name,
    created_at: 1_700_000_000_000,
    storage_version: 1,
    parent_session_id: null,
    workspace: { id: 'ws-1', root: '/home/fishy/Avid', name: 'Avid', default_permission: 'manual' },
    message_count: 3,
    active_run_id: null,
    truncated_tail: false,
  }
}

export function defaultWorkspace(id: string, root: string, name: string): WorkspaceSummary {
  return {
    id,
    root,
    name,
    created_at: 1_700_000_000_000,
    last_used_at: 1_700_000_000_000,
    default_permission: 'manual',
    is_default: id === 'ws-1',
  }
}

export function defaultBranches(): unknown {
  return {
    session_id: 's-1',
    branches: [
      { name: 'main', tip_entry_id: 'e1', entry_count: 3, is_default: true, usage: null },
    ],
  }
}
