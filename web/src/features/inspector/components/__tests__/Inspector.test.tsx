// @vitest-environment jsdom
/**
 * Inspector 的用例。
 *
 * 三条：页签切换回调、`diff` 不可用时该页签禁用（点了也不回调）、
 * 以及两种空态文案必须分得开——"还没选中任何工具"和"选中的工具本来就没有改动"
 * 是完全不同的两件事，混成一句话会让用户以为工具没改东西（其实可能是没选）。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Inspector } from '../Inspector'
import type { InspectorProps, InspectorSelection } from '../Inspector'

afterEach(cleanup)

const WITH_DIFF: InspectorSelection = {
  title: 'files',
  value: { path: 'a.txt', old_string: 'a', new_string: 'A' },
  content: '已写入',
  diff: { before: 'a\nb', after: 'A\nb' },
}

const WITHOUT_DIFF: InspectorSelection = {
  title: 'shell',
  value: { command: 'ls' },
  content: 'a.txt',
}

function renderInspector(overrides: Partial<InspectorProps> = {}): void {
  render(
    <Inspector
      selection={WITH_DIFF}
      tab="content"
      onTabChange={() => {}}
      onClose={() => {}}
      {...overrides}
    />,
  )
}

describe('Inspector 页签', () => {
  it('三个页签都在，且当前页签被选中', () => {
    renderInspector({ tab: 'json' })

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      '结果',
      '改动',
      '参数',
    ])
    expect(screen.getByRole('tab', { name: '参数' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: '结果' }).getAttribute('aria-selected')).toBe('false')
  })

  it('点页签发回调', () => {
    const onTabChange = vi.fn()
    renderInspector({ onTabChange })

    fireEvent.click(screen.getByRole('tab', { name: '改动' }))
    expect(onTabChange).toHaveBeenCalledWith('diff')

    fireEvent.click(screen.getByRole('tab', { name: '参数' }))
    expect(onTabChange).toHaveBeenCalledWith('json')
  })

  it('没有 diff 时「改动」页签禁用，点了也不回调', () => {
    const onTabChange = vi.fn()
    renderInspector({ selection: WITHOUT_DIFF, onTabChange })

    const diffTab = screen.getByRole('tab', { name: '改动' }) as HTMLButtonElement
    expect(diffTab.disabled).toBe(true)

    fireEvent.click(diffTab)
    expect(onTabChange).not.toHaveBeenCalled()
  })

  it('标题用选中项的名字，没有选中项时回落成「检查器」', () => {
    const { rerender } = render(
      <Inspector selection={WITH_DIFF} tab="content" onTabChange={vi.fn()} onClose={vi.fn()} />,
    )
    expect(screen.getByText('files')).toBeTruthy()

    rerender(<Inspector selection={null} tab="content" onTabChange={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText('检查器')).toBeTruthy()
  })

  it('关闭按钮走 onClose', () => {
    const onClose = vi.fn()
    renderInspector({ onClose })

    fireEvent.click(screen.getByRole('button', { name: '关闭检查器' }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('Inspector 内容', () => {
  it('结果页签显示 content', () => {
    renderInspector({ tab: 'content' })

    expect(screen.getByText('已写入')).toBeTruthy()
  })

  it('参数页签显示 JSON', () => {
    renderInspector({ tab: 'json' })

    expect(screen.getByText(/"path": "a.txt"/)).toBeTruthy()
  })

  it('改动页签显示行级差异', () => {
    renderInspector({ tab: 'diff' })

    expect(screen.getByText(/^\+A$/)).toBeTruthy()
    expect(screen.getByText(/^-a$/)).toBeTruthy()
  })

  it('没选中任何东西：空态说的是"还没选中"', () => {
    renderInspector({ selection: null })

    expect(screen.getByText(/还没有选中工具调用/)).toBeTruthy()
  })

  it('选中了但没有 diff：空态说的是"没有可比较的改动"', () => {
    // 父层把 tab 留在 diff 上（比如上一次选中的工具带 diff）：这时不能显示"还没选中"。
    renderInspector({ selection: WITHOUT_DIFF, tab: 'diff' })

    expect(screen.getByText(/没有可比较的改动/)).toBeTruthy()
    expect(screen.queryByText(/还没有选中工具调用/)).toBeNull()
  })
})
