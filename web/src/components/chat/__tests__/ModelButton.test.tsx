// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModelButton } from '../ModelButton'
import type { ModelCandidate } from '../../../api/types'

/**
 * 模型选择（输入区）：默认跟随设置，可以只对「本次运行」换一个模型。
 * 候选来自内核的窗口表，不是提供商目录——这条语义写在弹层的说明里，别让用户以为
 * 列出来的都一定可用。
 */
describe('模型选择', () => {
  afterEach(cleanup)

  const base = {
    model: null,
    effective: 'deepseek-chat',
    known: ['deepseek-chat', 'deepseek-reasoner'],
    candidates: [] as ModelCandidate[],
    onChange: () => {},
  }

  it('默认显示「跟随设置」，并带出设置里解析到的模型名', () => {
    render(<ModelButton {...base} />)

    const chip = screen.getByRole('button', { name: /模型/ })
    expect(chip.textContent).toContain('跟随设置')
    expect(chip.textContent).toContain('deepseek-chat')
    expect(chip.getAttribute('aria-expanded')).toBe('false')
  })

  it('按下展开候选：跟随设置 + 内核认得的模型；当前项打勾', () => {
    render(<ModelButton {...base} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))

    const dialog = screen.getByRole('dialog', { name: '模型' })
    expect(dialog.textContent).toContain('跟随设置')
    expect(dialog.textContent).toContain('deepseek-reasoner')
    expect(dialog.textContent).toContain('内核认得的模型')  // 说明候选的来源
    expect(screen.getByRole('button', { name: /模型/ }).getAttribute('aria-expanded')).toBe('true')
  })

  it('选一个模型 → onChange(名字) 并收起', () => {
    const onChange = vi.fn()
    render(<ModelButton {...base} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))
    fireEvent.click(screen.getByText('deepseek-reasoner'))

    expect(onChange).toHaveBeenCalledWith('deepseek-reasoner')
    expect(screen.queryByRole('dialog', { name: '模型' })).toBeNull()
  })

  it('选了别的模型后，胶囊显示它；再选「跟随设置」回到 null', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ModelButton {...base} model="deepseek-reasoner" onChange={onChange} />)
    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('deepseek-reasoner')

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))
    fireEvent.click(screen.getByText('跟随设置'))

    expect(onChange).toHaveBeenCalledWith(null)
    rerender(<ModelButton {...base} model={null} onChange={onChange} />)
    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('跟随设置')
  })
})
