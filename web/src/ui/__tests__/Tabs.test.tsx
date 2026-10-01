// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '../Tabs'

describe('Tabs（报告 §7.4：sliding pill 标签页）', () => {
  afterEach(cleanup)

  it('渲染标签组，选中项带 aria-selected', () => {
    render(<Tabs items={['对话', '频道', '便笺']} value="对话" onChange={() => {}} />)

    expect(screen.getByRole('tab', { name: '对话' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: '频道' }).getAttribute('aria-selected')).toBe('false')
  })

  it('点击切换选中并触发回调；滑块（指示块）随选中移动', () => {
    const onChange = vi.fn()
    render(<Tabs items={['对话', '频道']} value="对话" onChange={onChange} />)

    fireEvent.click(screen.getByRole('tab', { name: '频道' }))
    expect(onChange).toHaveBeenCalledWith('频道')

    // 滑块已渲染并带位移样式（jsdom 无布局，宽度为 0 也应写出 transform）
    const pill = document.querySelector('[data-testid="tabs-pill"]') as HTMLElement
    expect(pill).toBeTruthy()
    expect(pill.style.transform).toContain('translateX')
  })
})
