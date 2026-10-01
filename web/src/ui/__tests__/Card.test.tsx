// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Card } from '../Card'

describe('Card（组件墙 §卡片）', () => {
  afterEach(cleanup)

  it('默认形态：抬升面 + 发丝线 + 衬线标题', () => {
    render(<Card title="纸本卡片">抬升面比主面更亮，用发丝线而非阴影分层。</Card>)

    const title = screen.getByText('纸本卡片')
    expect(title.className).toContain('font-serif')
    expect(screen.getByText(/抬升面比主面更亮/)).toBeTruthy()
    // 组件墙的简单卡是 5px（rounded-sm），8px 的 --radius-card 留给大面板
    expect(title.parentElement?.className).toContain('rounded-sm')
  })

  it('panel 变体用 8px 圆角（右栏大面板用）', () => {
    render(
      <Card radius="md" title="面板">
        内容
      </Card>,
    )

    expect(screen.getByText('面板').parentElement?.className).toContain('rounded-card')
  })
})
