// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CodeBlock, lineNumbersOf } from '../CodeBlock'

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
    // concat === source
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

describe('代码块 · 文件预览形态（路径行 + 行号）', () => {
  afterEach(cleanup)

  it('给了路径就多一行完整路径（截断显示，不换行）', () => {
    const { container } = render(
      <CodeBlock lang="python" text="import os" path="/home/me/proj/src/main.py" />,
    )

    expect(screen.getByText('/home/me/proj/src/main.py')).toBeTruthy()
    expect(container.querySelector('.truncate')?.textContent).toBe('/home/me/proj/src/main.py')
  })

  it('没给路径就不多那一行（正文里的围栏代码块不受影响）', () => {
    render(<CodeBlock lang="python" text="import os" />)

    expect(screen.queryByText('/home/me/proj/src/main.py')).toBeNull()
  })

  it('行号列与内容同列数、同字号（对不齐就等于没有行号）', () => {
    const { container } = render(<CodeBlock lang="python" text={'a = 1\nb = 2\nc = 3'} lineNumbers />)

    const gutter = container.querySelector('pre[aria-hidden]') as HTMLElement
    expect(gutter.textContent).toBe('1\n2\n3')
    expect(gutter.className).toContain('leading-[1.6]')
    expect(gutter.className).toContain('font-mono')
  })

  it('lineNumbersOf：尾随换行不多编号', () => {
    expect(lineNumbersOf('a\nb')).toEqual([1, 2])
    expect(lineNumbersOf('a\nb\n')).toEqual([1, 2])
    expect(lineNumbersOf('')).toEqual([1])
  })
})
