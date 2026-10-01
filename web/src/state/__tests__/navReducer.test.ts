/**
 * `navReducer` 的用例。
 *
 * 为什么这个 reducer 值得单独钉住：它有三处**跨域联动**——删会话要连带清掉选中项
 * 与检查器、刷新后选中项失效要回落、换分支要清掉检查器里的旧结果。
 * 这类"少清一处就留下悬空引用"的逻辑，靠人眼 review 很容易漏，写成断言才守得住。
 *
 * 默认 node 环境：reducer 是纯函数，不需要 jsdom。
 */

import { describe, expect, it } from 'vitest'

import type { SessionSummary } from '../../api/types'
import { initialNavState, navReducer } from '../navReducer'
import type { NavAction, NavState } from '../navReducer'

function session(id: string, createdAt: number): SessionSummary {
  return {
    id,
    name: `会话 ${id}`,
    created_at: createdAt,
    storage_version: 1,
    parent_session_id: null,
    workspace: null,
    message_count: 0,
    active_run_id: null,
    truncated_tail: false,
  }
}

/** 从初始态出发依次套用动作，模拟真实的事件序列。 */
function apply(...actions: NavAction[]): NavState {
  return actions.reduce(navReducer, initialNavState)
}

const LOADED: NavAction = { type: 'sessions/loaded', sessions: [session('a', 1), session('b', 2)] }

describe('navReducer', () => {
  it('列表到达时按 created_at 降序整理', () => {
    const state = apply({ type: 'sessions/loaded', sessions: [session('a', 1), session('b', 2)] })

    expect(state.sessions.map((item) => item.id)).toEqual(['b', 'a'])
  })

  it('首次装载时自动选中最近的一条（回落语义）', () => {
    const state = apply(LOADED)

    expect(state.activeSessionId).toBe('b')
  })

  it('刷新后当前会话已消失时回落到新的第一条', () => {
    const state = apply(LOADED, { type: 'session/selected', id: 'a' }, {
      type: 'sessions/loaded',
      sessions: [session('c', 3)],
    })

    // 'a' 不在新列表里 → 回落，而不是继续指向一个已不存在的 id。
    expect(state.activeSessionId).toBe('c')
  })

  it('刷新后当前会话仍在时保持选中不变', () => {
    const state = apply(LOADED, { type: 'session/selected', id: 'a' }, {
      type: 'sessions/loaded',
      sessions: [session('a', 1)],
    })

    expect(state.activeSessionId).toBe('a')
  })

  it('列表清空后选中项变为 null，而不是悬空 id', () => {
    const state = apply(LOADED, { type: 'sessions/loaded', sessions: [] })

    expect(state.activeSessionId).toBeNull()
  })

  it('删除当前会话时回落，并连带清掉检查器选中', () => {
    const state = apply(
      LOADED,
      { type: 'session/selected', id: 'b' },
      { type: 'inspector/opened', toolCallId: 't-1' },
      { type: 'session/removed', id: 'b' },
    )

    expect(state.sessions.map((item) => item.id)).toEqual(['a'])
    expect(state.activeSessionId).toBe('a')
    // 检查器里显示的是被删会话里的工具调用，必须一起清掉。
    expect(state.inspectorToolCallId).toBeNull()
  })

  it('删除非当前会话时不动选中项与检查器', () => {
    const state = apply(
      LOADED,
      { type: 'session/selected', id: 'b' },
      { type: 'inspector/opened', toolCallId: 't-1' },
      { type: 'session/removed', id: 'a' },
    )

    expect(state.activeSessionId).toBe('b')
    expect(state.inspectorToolCallId).toBe('t-1')
  })

  it('删除不存在的会话时返回同一个对象（不制造无意义的重渲染）', () => {
    const before = apply(LOADED)
    const after = navReducer(before, { type: 'session/removed', id: '不存在' })

    expect(after).toBe(before)
  })

  it('换会话时关掉检查器', () => {
    const state = apply(
      LOADED,
      { type: 'inspector/opened', toolCallId: 't-1' },
      { type: 'session/selected', id: 'a' },
    )

    expect(state.inspectorToolCallId).toBeNull()
  })

  it('换分支时关掉检查器', () => {
    const state = apply(
      LOADED,
      { type: 'inspector/opened', toolCallId: 't-1' },
      { type: 'branch/selected', branch: 'b2' },
    )

    expect(state.branch).toBe('b2')
    expect(state.inspectorToolCallId).toBeNull()
  })

  it('打开检查器时页签回到 content', () => {
    const state = apply(
      LOADED,
      { type: 'inspector/tab', tab: 'json' },
      { type: 'inspector/opened', toolCallId: 't-1' },
    )

    // 停在 json 会让"新选中的工具没有 json"看起来像坏了，所以统一回 content。
    expect(state.inspectorTab).toBe('content')
  })

  it('收起状态可来回切换', () => {
    expect(apply({ type: 'nav/toggled' }).navCollapsed).toBe(true)
    expect(apply({ type: 'nav/toggled' }, { type: 'nav/toggled' }).navCollapsed).toBe(false)
  })
})
