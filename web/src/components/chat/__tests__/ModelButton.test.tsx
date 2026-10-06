// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModelButton } from '../ModelButton'
import type { ModelCandidate } from '../../../api/types'

/**
 * 模型选择（输入区）：默认跟随设置，可以只对「本次运行」换一个模型。
 * 候选只有 BYOK（用户在「设置 → 模型」里配的提供商）——内核不预置任何模型选项，
 * 没配 BYOK 时弹层里给的是去设置里添加的指引，而不是一张预置清单。
 */
describe('模型选择', () => {
  afterEach(cleanup)

  const byok: ModelCandidate[] = [
    { ref: 'command/deepseek/deepseek-v4.1-flash', label: 'command · deepseek-v4.1-flash' },
  ]
  const base = {
    model: null,
    effective: 'deepseek/deepseek-v4.1-flash',
    candidates: byok,
    onChange: () => {},
  }

  it('默认显示「跟随设置」，并带出设置里解析到的模型名', () => {
    render(<ModelButton {...base} />)

    const chip = screen.getByRole('button', { name: /模型/ })
    expect(chip.textContent).toContain('跟随设置')
    expect(chip.textContent).toContain('deepseek/deepseek-v4.1-flash')
    expect(chip.getAttribute('aria-expanded')).toBe('false')
  })

  it('展开候选：跟随设置 + BYOK 提供商；当前项打勾', () => {
    render(<ModelButton {...base} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))

    const dialog = screen.getByRole('dialog', { name: '模型' })
    expect(dialog.textContent).toContain('跟随设置')
    expect(dialog.textContent).toContain('BYOK 提供商')
    expect(dialog.textContent).toContain('command · deepseek-v4.1-flash')
    expect(screen.getByRole('button', { name: /模型/ }).getAttribute('aria-expanded')).toBe('true')
  })

  it('选一个 BYOK 候选 → onChange(ref) 并收起', () => {
    const onChange = vi.fn()
    render(<ModelButton {...base} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))
    fireEvent.click(screen.getByText('command · deepseek-v4.1-flash'))

    expect(onChange).toHaveBeenCalledWith('command/deepseek/deepseek-v4.1-flash')
    expect(screen.queryByRole('dialog', { name: '模型' })).toBeNull()
  })

  it('没有 BYOK 候选时不列任何预置模型，给出去设置里添加的指引', () => {
    render(<ModelButton {...base} candidates={[]} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))

    const dialog = screen.getByRole('dialog', { name: '模型' })
    expect(dialog.textContent).toContain('设置 → 模型')
    // 预置模型（gpt-4o / claude-* 之类）不再出现
    expect(screen.queryByText('gpt-4o')).toBeNull()
    expect(screen.queryByText('deepseek-chat')).toBeNull()
  })

  it('选了别的模型后，胶囊显示它；再选「跟随设置」回到 null', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ModelButton {...base} model="command/deepseek/deepseek-v4.1-flash" onChange={onChange} />)
    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('deepseek-v4.1-flash')

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))
    fireEvent.click(screen.getByText('跟随设置'))

    expect(onChange).toHaveBeenCalledWith(null)
    rerender(<ModelButton {...base} model={null} onChange={onChange} />)
    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('跟随设置')
  })
})
