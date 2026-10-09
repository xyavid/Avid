// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AssistantMessage } from '../AssistantMessage'
import { UserBubble } from '../UserBubble'

describe('UserBubble（组件墙 §消息与工具卡）', () => {
  afterEach(cleanup)

  it('右对齐、accent 浅垫底、主文字色', () => {
    const { container } = render(<UserBubble>帮我读 pyproject.toml</UserBubble>)

    // Text lands in markdown paragraphs; the bubble styles sit on the outer element.
    const bubble = container.querySelector('.bg-accent-light')
    expect(bubble?.textContent).toContain('帮我读 pyproject.toml')
    expect(bubble?.parentElement?.className).toContain('justify-end')
  })

  it('也用 markdown 渲染（用户贴的代码块/列表不该原样显示标记符）', () => {
    const { container } = render(<UserBubble>{'看这个：\n\n```ts\nconst a = 1\n```\n\n- 甲\n- 乙'}</UserBubble>)

    expect(container.querySelector('pre code')?.textContent).toBe('const a = 1')
    expect([...container.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['甲', '乙'])
    expect(container.textContent).not.toContain('```')
  })
})

describe('AssistantMessage（报告 §7.3：头像行 + 正文分列）', () => {
  afterEach(cleanup)

  it('头像 + 名称行 + 衬线正文（正文走 markdown 渲染）', () => {
    const { container } = render(<AssistantMessage>好，我去读文件。</AssistantMessage>)

    expect(screen.getByText('Avid')).toBeTruthy()
    // The body wrapper keeps the serif stack; text lands in markdown paragraphs and inherits it.
    const body = container.querySelector('.serif-text')
    expect(body?.textContent).toContain('好，我去读文件。')
    expect(body?.querySelector('p')?.textContent).toBe('好，我去读文件。')
  })

  it('markdown 生效：标题、列表、行内代码落成元素，原文不显示标记符', () => {
    const { container } = render(<AssistantMessage>{'## 结果\n\n- 甲\n- 乙\n\n用 `npm test` 跑。'}</AssistantMessage>)

    expect(container.querySelector('h2')?.textContent).toBe('结果')
    expect([...container.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['甲', '乙'])
    expect(container.querySelector('p code')?.textContent).toBe('npm test')
    expect(container.textContent).not.toContain('##')
    expect(container.textContent).not.toContain('`')
  })

  it('流式态无文本：显示生成中的呼吸点，不渲染正文', () => {
    render(<AssistantMessage streaming />)

    expect(screen.getByLabelText('生成中')).toBeTruthy()
  })

  it('续段（showHead=false）不重复标识行，正文照常渲染', () => {
    const { container } = render(<AssistantMessage showHead={false}>接着说后半段。</AssistantMessage>)

    expect(screen.queryByText('Avid')).toBeNull()
    expect(container.querySelector('.serif-text')?.textContent).toContain('接着说后半段。')
  })

  it('流式态有部分文本：渲染正文 + 光标呼吸尾标', () => {
    const { container } = render(<AssistantMessage streaming>正在生成</AssistantMessage>)

    expect(screen.getByText(/正在生成/)).toBeTruthy()
    expect(container.querySelector('.serif-text')).toBeTruthy()
    // With text present the three-dot placeholder is gone.
    expect(screen.queryByLabelText('生成中')).toBeNull()
  })
})
