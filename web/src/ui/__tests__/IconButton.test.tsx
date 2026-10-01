// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { IconButton } from '../IconButton'

describe('IconButton（报告 §7.6：26px 图标按钮）', () => {
  afterEach(cleanup)

  it('渲染指定图标，aria-label 必填（图标按钮没有文字）', () => {
    render(<IconButton icon="pin" label="置顶" />)

    const btn = screen.getByRole('button', { name: '置顶' })
    expect(btn.querySelector('svg')).toBeTruthy()
    expect(btn.className).toContain('h-[26px]')
    expect(btn.className).toContain('rounded-sm')
  })

  it('hover 底用 overlay-light（纸面轻压痕）', () => {
    render(<IconButton icon="archive" label="归档" />)

    expect(screen.getByRole('button').className).toContain('hover:bg-overlay-light')
  })

  it('点击回调生效', () => {
    const onClick = vi.fn()
    render(<IconButton icon="pin" label="置顶" onClick={onClick} />)

    screen.getByRole('button').click()
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('primary 变体：accent 填充（composer 发送钮）', () => {
    render(<IconButton icon="send" label="发送" variant="primary" />)

    const classes = screen.getByRole('button').className.split(' ')
    expect(classes).toContain('bg-accent')
    expect(classes).toContain('text-card')
    expect(classes).not.toContain('hover:bg-overlay-light')
  })
})
