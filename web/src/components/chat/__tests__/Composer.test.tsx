// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Composer } from '../Composer'

describe('Composer（参考图输入区）', () => {
  afterEach(cleanup)

  it('裸输入框就位；发送钮禁用并注明阶段 5 接线', () => {
    render(<Composer />)

    const input = screen.getByPlaceholderText('给 Avid 发消息…') as HTMLInputElement
    expect(input.disabled).toBe(true)

    const send = screen.getByRole('button', { name: '发送' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(send.className).toContain('bg-accent')
    expect(send.title).toContain('阶段 5')
  })

  it('附加类图标按钮在左侧、发送在右侧', () => {
    const { container } = render(<Composer />)

    const buttons = [...container.querySelectorAll('button')]
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(['附加', '附件', '发送'])
  })
})
