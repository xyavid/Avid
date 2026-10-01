// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AssistantMessage } from '../AssistantMessage'
import { UserBubble } from '../UserBubble'

describe('UserBubble（组件墙 §消息与工具卡）', () => {
  afterEach(cleanup)

  it('右对齐、accent 浅垫底、主文字色', () => {
    render(<UserBubble>帮我读 pyproject.toml</UserBubble>)

    const bubble = screen.getByText('帮我读 pyproject.toml')
    expect(bubble.className).toContain('bg-accent-light')
    expect(bubble.parentElement?.className).toContain('justify-end')
  })
})

describe('AssistantMessage（报告 §7.3：头像行 + 正文分列）', () => {
  afterEach(cleanup)

  it('头像 + 名称行 + 衬线正文', () => {
    render(<AssistantMessage>好，我去读文件。</AssistantMessage>)

    expect(screen.getByText('Avid')).toBeTruthy()
    const body = screen.getByText('好，我去读文件。')
    expect(body.className).toContain('serif-text')
  })

  it('流式态无文本：显示生成中的呼吸点，不渲染正文', () => {
    render(<AssistantMessage streaming />)

    expect(screen.getByLabelText('生成中')).toBeTruthy()
  })

  it('流式态有部分文本：渲染正文 + 光标呼吸尾标', () => {
    const { container } = render(<AssistantMessage streaming>正在生成</AssistantMessage>)

    expect(screen.getByText(/正在生成/)).toBeTruthy()
    expect(container.querySelector('.serif-text')).toBeTruthy()
    // 有文本时不再显示三点占位
    expect(screen.queryByLabelText('生成中')).toBeNull()
  })
})
