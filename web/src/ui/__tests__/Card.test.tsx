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
    // the simple card is 5px (rounded-sm); the 8px --radius-card is for large panels
    expect(title.closest('div')?.parentElement?.className).toContain('rounded-sm')
  })

  it('panel 变体用 8px 圆角（右栏大面板用）', () => {
    render(
      <Card radius="md" title="面板">
        内容
      </Card>,
    )

    expect(screen.getByText('面板').closest('div')?.parentElement?.className).toContain('rounded-card')
  })

  it('actions 插槽渲染在标题行右侧（如工作区卡的「新增」按钮）', () => {
    render(
      <Card title="工作区" actions={<button type="button">新增工作区</button>}>
        内容
      </Card>,
    )

    const header = screen.getByText('工作区').parentElement
    expect(header?.textContent).toContain('工作区')
    expect(header?.querySelector('button')?.textContent).toBe('新增工作区')
  })
})
