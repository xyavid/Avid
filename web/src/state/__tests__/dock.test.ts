// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useDock } from '../dock'

const KEY = 'avid.dock.column'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('useDock（右侧 dock 的界面域状态）', () => {
  it('默认收起：右列不是常驻栏（阶段 54）', () => {
    const { result } = renderHook(() => useDock())

    expect(result.current.open).toBe(false)
    expect(result.current.active).toBe('files')
    expect(result.current.choosing).toBe(false)
  })

  it('手动打开先给选择页；选一个面板直接进去', () => {
    const { result } = renderHook(() => useDock())

    act(() => result.current.toggle())
    expect(result.current.open).toBe(true)
    expect(result.current.choosing).toBe(true)

    act(() => result.current.select('subagents'))
    expect(result.current.open).toBe(true)
    expect(result.current.choosing).toBe(false)
    expect(result.current.active).toBe('subagents')
  })

  it('由代码选中（点子智能体卡那种）直接进面板，不多问一层', () => {
    const { result } = renderHook(() => useDock())

    act(() => result.current.select('subagents'))

    expect(result.current.open).toBe(true)
    expect(result.current.choosing).toBe(false)
  })

  it('开合与激活面板进存储；选择页不进（它是一次会话里的态度，不是偏好）', () => {
    const { result } = renderHook(() => useDock())
    act(() => result.current.select('terminal'))

    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}')).toEqual({ open: true, active: 'terminal' })
    expect(localStorage.getItem(KEY)).not.toContain('choosing')
  })

  it('存储里的旧值/坏值回落默认：unknown 面板名 → files，open 非得 true 才算开', () => {
    localStorage.setItem(KEY, JSON.stringify({ open: 'yes', active: 'browser' }))
    const { result } = renderHook(() => useDock())

    expect(result.current.open).toBe(false)
    expect(result.current.active).toBe('files')
  })
})
