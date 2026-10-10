// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EffortButton } from '../EffortButton'

describe('EffortButton（输入区推理强度）', () => {
  afterEach(cleanup)

  const base = { effort: null, levels: ['low', 'medium', 'high', 'max'], onChange: () => {} }

  it('胶囊带当前档；没设时是「不设」', () => {
    const { rerender } = render(<EffortButton {...base} />)
    expect(screen.getByRole('button', { name: /推理强度/ }).textContent).toContain('不设')

    rerender(<EffortButton {...base} effort="high" />)
    expect(screen.getByRole('button', { name: /推理强度/ }).textContent).toContain('high')
  })

  it('点开是「不设 + 模型声明的档位」，当前档带勾', () => {
    render(<EffortButton {...base} effort="high" />)
    fireEvent.click(screen.getByRole('button', { name: /推理强度/ }))

    const dialog = screen.getByRole('dialog', { name: '推理强度' })
    for (const level of ['不设', 'low', 'medium', 'high', 'max']) {
      expect(dialog.textContent).toContain(level)
    }
    // One check mark: the current level only.
    expect(dialog.querySelectorAll('svg').length).toBe(1)
  })

  it('选一档回调那个档并收起；选「不设」回调 null', () => {
    const onChange = vi.fn()
    render(<EffortButton {...base} effort="high" onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /推理强度/ }))
    fireEvent.click(within(screen.getByRole('dialog', { name: '推理强度' })).getByText('max'))
    expect(onChange).toHaveBeenCalledWith('max')
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /推理强度/ }))
    fireEvent.click(within(screen.getByRole('dialog', { name: '推理强度' })).getByText('不设'))
    expect(onChange).toHaveBeenLastCalledWith(null)
  })

  it('宽度随文字走：档位名不截断、不设上限（档位名由 provider 自定，长短不一）', () => {
    render(<EffortButton {...base} effort="high" />)

    const label = screen.getByRole('button', { name: /推理强度/ }).querySelector('span')
    expect(label?.className ?? '').not.toContain('truncate')
    expect(label?.className ?? '').not.toContain('max-w')
  })

  it('模型没声明档位：胶囊仍在（否则用户找不到它），点开说明去哪声明', () => {
    render(<EffortButton {...base} levels={[]} />)

    const chip = screen.getByRole('button', { name: /推理强度/ })
    expect(chip.textContent).toContain('未声明')

    fireEvent.click(chip)
    const dialog = screen.getByRole('dialog', { name: '推理强度' })
    expect(dialog.textContent).toContain('设置')
    expect(dialog.textContent).toContain('强度档位')
    // Nothing to pick: no 不设 and no level rows.
    expect(within(dialog).queryByText('不设')).toBeNull()
  })
})
