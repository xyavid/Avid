// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CodeBlock } from '../CodeBlock'

describe('代码块（高亮）', () => {
  afterEach(cleanup)

  it('高亮成带类名的 span，文本仍然逐字完整', () => {
    const code = 'def add(a, b):\n    return a + 1  # 求和'
    const { container } = render(<CodeBlock lang="python" text={code} />)

    const spans = container.querySelectorAll('pre code span')
    expect(spans.length).toBeGreaterThan(3)
    expect(container.querySelector('.text-syntax-keyword')?.textContent).toBe('def')
    expect(container.querySelector('.text-syntax-comment')?.textContent).toBe('# 求和')
    expect(container.querySelector('.text-syntax-number')?.textContent).toBe('1')
    // 拼回去 == 原文
    expect(container.querySelector('pre code')?.textContent).toBe(code)
  })

  it('未知语言不染色，但内容一字不少', () => {
    const { container } = render(<CodeBlock lang="不认识的" text={'随便\n什么'} />)

    expect(container.querySelector('pre code')?.textContent).toBe('随便\n什么')
    expect(container.querySelector('.text-syntax-keyword')).toBeNull()
  })

  it('复制按钮复制的是**原文**，不是高亮后的碎片', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const code = 'const a = 1 // c'
    render(<CodeBlock lang="ts" text={code} />)

    fireEvent.click(screen.getByRole('button', { name: /复制/ }))

    expect(writeText).toHaveBeenCalledWith(code)
  })

  it('没有语言标时显示「文本」', () => {
    render(<CodeBlock lang={null} text={'plain'} />)

    expect(screen.getByText('文本')).toBeTruthy()
  })
})
