// @vitest-environment jsdom
/**
 * 顶部条胶囊标签的契约用例。
 *
 * 三条都是"没测就会静默退化"的：`aria-selected` 丢了读屏用户不知道在哪一档、
 * 点击回调传错 key 会切到别的面、禁用页签仍可点会让用户撞进一个不存在的面。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SurfaceTabs } from '../SurfaceTabs'
import type { SurfaceTab } from '../SurfaceTabs'

afterEach(cleanup)

const TABS: SurfaceTab[] = [
  { key: 'chat', label: '聊天' },
  { key: 'channel', label: '频道' },
  { key: 'soon', label: '置顶', disabled: true },
]

describe('SurfaceTabs', () => {
  it('选中项标 aria-selected=true，其余为 false', () => {
    render(<SurfaceTabs tabs={TABS} active="chat" onChange={vi.fn()} aria-label="工作区视图" />)

    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(3)
    expect(tabs[0]?.getAttribute('aria-selected')).toBe('true')
    expect(tabs[1]?.getAttribute('aria-selected')).toBe('false')
    expect(screen.getByRole('tablist').getAttribute('aria-label')).toBe('工作区视图')
  })

  it('点击未选中页签把它的 key 交给回调', () => {
    const onChange = vi.fn()
    render(<SurfaceTabs tabs={TABS} active="chat" onChange={onChange} aria-label="工作区视图" />)

    fireEvent.click(screen.getByRole('tab', { name: '频道' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('channel')
  })

  it('禁用的页签不可点、说明原因，且不触发回调', () => {
    const onChange = vi.fn()
    render(<SurfaceTabs tabs={TABS} active="chat" onChange={onChange} aria-label="工作区视图" />)

    const disabled = screen.getByRole('tab', { name: '置顶' })
    expect(disabled.hasAttribute('disabled')).toBe(true)
    expect(disabled.getAttribute('title')).not.toBeNull()

    fireEvent.click(disabled)
    expect(onChange).not.toHaveBeenCalled()
  })
})
