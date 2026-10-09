// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Badge } from '../Badge'
import { Tag } from '../Tag'

describe('Tag（组件墙 §标签）', () => {
  afterEach(cleanup)

  it('accent 变体：accent 浅底 + 深字', () => {
    render(<Tag>accent 标签</Tag>)

    const el = screen.getByText('accent 标签')
    expect(el.className).toContain('bg-accent-light')
    expect(el.className).toContain('text-accent-hover')
  })

  it('danger 变体：墙的 0.08 透明度底 + 深朱字', () => {
    render(<Tag variant="danger">danger</Tag>)

    const el = screen.getByText('danger')
    expect(el.className).toContain('text-danger')
    expect(el.className).toContain('bg-danger/[0.08]')
  })

  it('neutral 变体', () => {
    render(<Tag variant="neutral">中性</Tag>)

    expect(screen.getByText('中性').className).toContain('bg-sidebar')
  })
})

describe('Badge（组件墙 §徽章）', () => {
  afterEach(cleanup)

  it('带 check 图标的徽章', () => {
    render(<Badge icon="check">已编译</Badge>)

    const badge = screen.getByText('已编译').closest('span')
    expect(badge?.querySelector('svg')).toBeTruthy()
    // linear-icon discipline: stroke, never a filled shape
    expect(badge?.querySelector('svg')?.getAttribute('fill')).toBe('none')
  })

  it('danger 徽章不带图标也成立', () => {
    render(<Badge variant="danger">danger</Badge>)

    expect(screen.getByText('danger').closest('span')?.className).toContain('text-danger')
  })
})
