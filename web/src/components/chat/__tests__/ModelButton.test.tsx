// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModelButton } from '../ModelButton'
import type { ModelCandidate } from '../../../api/types'

/**
 * Model chip: candidates are only BYOK — with none configured the popover points at settings
 * instead of listing presets, and no model means no server-side default.
 */
describe('模型选择（阶段 54：由用户自己选，没有「跟随设置」这一档）', () => {
  afterEach(cleanup)

  const byok: ModelCandidate[] = [
    {
      ref: 'command/deepseek/deepseek-v4.1-flash',
      label: 'command · deepseek-v4.1-flash',
      reasoning_efforts: ['low', 'medium', 'high', 'max'],
    },
    { ref: 'stub/stub-chat', label: '本地假端点 · stub-chat' },
  ]
  const base = { model: null, candidates: byok, onChange: () => {} }

  it('还没选就是「选择模型」：没有跟随设置那一行，也不带出任何服务端模型名', () => {
    render(<ModelButton {...base} />)

    const chip = screen.getByRole('button', { name: /模型/ })
    expect(chip.textContent).toContain('选择模型')
    expect(chip.getAttribute('aria-expanded')).toBe('false')
  })

  it('没有常驻边框：框只在悬停/展开时出现，没选模型靠强调色而不是边框', () => {
    const { rerender } = render(<ModelButton {...base} model="stub/stub-chat" />)
    const classes = () => screen.getByRole('button', { name: /模型/ }).className.split(' ')

    expect(classes()).not.toContain('border-hairline')
    expect(classes()).not.toContain('border-hair')
    expect(classes()).toContain('hover:bg-overlay-light')

    // The accent tint carries "not chosen yet" now that the outline is gone.
    rerender(<ModelButton {...base} />)
    expect(classes()).not.toContain('border-accent')
    expect(classes()).toContain('text-accent')
    expect(classes()).toContain('hover:bg-accent-light')
  })

  it('展开就是候选列表：不列"跟随设置"，也没有服务端解析的模型名', () => {
    render(<ModelButton {...base} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))

    const dialog = screen.getByRole('dialog', { name: '模型' })
    expect(dialog.textContent).toContain('command · deepseek-v4.1-flash')
    expect(dialog.textContent).toContain('本地假端点 · stub-chat')
    expect(dialog.textContent).not.toContain('跟随设置')
  })

  it('选一个候选 → onChange(ref) 并收起', () => {
    const onChange = vi.fn()
    render(<ModelButton {...base} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))
    fireEvent.click(screen.getByText('command · deepseek-v4.1-flash'))

    expect(onChange).toHaveBeenCalledWith('command/deepseek/deepseek-v4.1-flash')
    expect(screen.queryByRole('dialog', { name: '模型' })).toBeNull()
  })

  it('已选中：胶囊显示候选的展示名，弹层里那一项打勾', () => {
    render(<ModelButton {...base} model="stub/stub-chat" />)

    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('本地假端点 · stub-chat')

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))
    const dialog = screen.getByRole('dialog', { name: '模型' })
    expect(dialog.textContent).toContain('stub/stub-chat')
  })

  it('选中的 ref 不在候选里（设置里删了）：原样显示 ref，不假装它是候选', () => {
    render(<ModelButton {...base} model="ghost/removed" />)

    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('ghost/removed')
  })

  it('推理强度不再挂在模型胶囊里：它有自己的控件，胶囊只留模型名与箭头', () => {
    render(<ModelButton {...base} model="command/deepseek/deepseek-v4.1-flash" />)

    const chip = screen.getByRole('button', { name: /模型/ })
    expect(chip.textContent).toContain('command · deepseek-v4.1-flash')

    fireEvent.click(chip)
    expect(screen.getByRole('dialog', { name: '模型' }).textContent).not.toContain('推理强度')
  })

  it('没有 BYOK 候选时不列任何预置模型，给出去设置里添加的指引', () => {
    render(<ModelButton {...base} candidates={[]} />)

    fireEvent.click(screen.getByRole('button', { name: /模型/ }))

    const dialog = screen.getByRole('dialog', { name: '模型' })
    expect(dialog.textContent).toContain('设置 → 模型')
    expect(screen.queryByText('gpt-4o')).toBeNull()
    expect(screen.queryByText('deepseek-chat')).toBeNull()
  })
})
