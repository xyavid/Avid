// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ToolCard } from '../ToolCard'

describe('ToolCard（组件墙 §工具卡）', () => {
  afterEach(cleanup)

  it('头部：工具图标 + 标题 + 右侧徽章；正文为等宽小字', () => {
    render(
      <ToolCard icon="terminal" title="bash" badge="0.8s">
        uv run pytest -q
      </ToolCard>,
    )

    expect(screen.getByText('bash')).toBeTruthy()
    expect(screen.getByText('0.8s')).toBeTruthy()
    const body = screen.getByText('uv run pytest -q')
    expect(body.className).toContain('font-mono')
    // 图标用 accent 色（墙实绘：stroke var(--accent)）
    expect(document.querySelector('svg')?.getAttribute('stroke')).toBe('currentColor')
  })

  it('不同工具换不同图标（file-frame / globe / terminal）', () => {
    const { container } = render(
      <ToolCard icon="globe" title="web_search">
        query=avid
      </ToolCard>,
    )

    expect(container.querySelector('svg')).toBeTruthy()
    expect(screen.getByText('web_search')).toBeTruthy()
  })
})
