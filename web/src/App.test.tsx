// @vitest-environment jsdom
/**
 * 装配层的用例：守住"根组件能把已有数据渲染出来"这条链。
 *
 * 它**不再是**占位壳的 smoke 用例（阶段 32 的占位壳已被本次重构替换）。
 * 测试用**注入数据**（`App` 的 `data` 注入点），不发一个请求：
 * 与后端尚未联调时，"能不能渲染"这件事也必须可回答，否则只能靠起服务来验证。
 *
 * 覆盖三件事，都是装配层独有、别的层测不到的风险：
 *   1. 没有会话时给空态而不是白屏；
 *   2. 会话列表非空时回落选中最新的一条并渲染对话页；
 *   3. 没有可用工作区时**禁用**新建（不发一个注定 400 `workspace_required` 的请求）。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import type { Meta, SessionSummary, WorkspaceSummary } from './api/types'
import type { SessionData } from './state/useSessionData'

/*
 * `useSessionData` 在测试里也会被真实调用一次（Provider 里钩子必须无条件调用）。
 * 把全局 `fetch` 换成永不 resolve 的桩：它既不污染断言，也不会在 jsdom 里
 * 抛未处理的拒绝。真实数据层的失败只写进它自己的 `error`，与注入视图无关。
 */
beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => undefined)),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const META: Meta = {
  api_version: 1,
  features: {},
  event_types: [],
  capabilities: {
    tools: [],
    skills: [],
    model: 'test-model',
    workspace: '/tmp/workspace',
    workspace_picker: null,
    sandbox: { backend: 'bwrap', available: true, network: false, reason: null, landlock_abi: null },
  },
  stream: { heartbeat_seconds: 15, terminal_fallback_seconds: 30, replay_buffer_size: 256 },
  build: { git_sha: null, built_at: null, source: 'test' },
}

function session(id: string, name: string, createdAt: number): SessionSummary {
  return {
    id,
    name,
    created_at: createdAt,
    storage_version: 1,
    parent_session_id: null,
    workspace: {
      id: 'ws-1',
      root: '/tmp/workspace',
      name: '测试工作区',
      default_permission: 'manual',
    },
    message_count: 0,
    active_run_id: null,
    truncated_tail: false,
  }
}

const WORKSPACE: WorkspaceSummary = {
  id: 'ws-1',
  root: '/tmp/workspace',
  name: '测试工作区',
  created_at: 0,
  last_used_at: 0,
  default_permission: 'manual',
  is_default: true,
}

function dataWith(overrides: Partial<SessionData> = {}): SessionData {
  return {
    meta: META,
    sessions: [],
    workspaces: [WORKSPACE],
    loading: false,
    error: null,
    defaultWorkspace: '/tmp/workspace',
    refresh: async () => undefined,
    create: async () => {
      throw new Error('用例不应触发新建')
    },
    rename: async () => undefined,
    remove: async () => undefined,
    ...overrides,
  }
}

function renderWith(data: SessionData) {
  /*
   * 直接给 `App` 传注入数据，**不要**在外面再包一层 `<AppProvider>`：
   * `App` 内部已经装了 Provider，外面那层会被内层用**真实**数据层覆盖掉
   * （真实数据层在 jsdom 里是空的 → 页面渲染成空态，而上下文探针却读得到注入数据）。
   * 这个坑真实发生过，详见 `AppProps.data` 的注释。
   */
  return render(<App data={data} />)
}

describe('App 装配', () => {
  it('没有会话时给空态，并给出下一步动作', async () => {
    renderWith(dataWith({ sessions: [] }))

    expect(await screen.findByText('还没有会话')).toBeTruthy()
    expect(screen.getByRole('button', { name: '新建会话' })).toBeTruthy()
  })

  it('会话列表非空时回落选中最新的一条，渲染对话页而不是空态', async () => {
    const older = session('s-old', '旧会话', 1_000)
    const newer = session('s-new', '新会话', 2_000)

    /*
     * 故意传**乱序**列表（旧的在前）：reducer 的 `sessions/loaded` 要按
     * `created_at` 降序整理并回落到第一项。这条用例钉住的正是那个回落。
     */
    renderWith(dataWith({ sessions: [older, newer] }))

    /*
     * 断言落在**对话页头部的 h1（会话名）**上。
     *
     * 为什么不用「还没有会话」那句话来判定空态已消失：左栏 `SessionNav` 在列表为空时
     * 也有自己的空态文案（列表为空时它就该这么说）。同一页面上可能同时存在两处同名文本，
     * 用那句话判定会把左栏的正常空态误判成失败。
     * 也不用「新建会话」按钮：它同样是两处同名（主区落地页 + 左栏头部）。
     * `h1` 只有对话页头部会渲染，是这里唯一无歧义的锚点。
     */
    const heading = await screen.findByRole('heading', { level: 1 })
    expect(heading.textContent).toBe('新会话')
  })

  it('没有可用工作区时禁用新建，并说明原因', async () => {
    renderWith(dataWith({ sessions: [], workspaces: [], defaultWorkspace: null }))

    const button = await screen.findByRole('button', { name: '新建会话' })
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(screen.getByText(/avid workspace add/)).toBeTruthy()
  })
})
