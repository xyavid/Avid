// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ToolCard } from '../ToolCard'

afterEach(cleanup)

describe('ToolCard（双形态：折叠单行 ↔ 展开完整卡）', () => {
  it('默认折叠为单行：图标 + 标题 + 预览 + 状态，不渲染正文', () => {
    render(
      <ToolCard icon="terminal" title="bash" preview="uv run pytest -q" status="ok">
        长输出内容
      </ToolCard>,
    )

    expect(screen.getByText('bash')).toBeTruthy()
    expect(screen.getByText('uv run pytest -q')).toBeTruthy()
    expect(screen.getByLabelText('成功')).toBeTruthy()
    expect(screen.queryByText('长输出内容')).toBeNull()
  })

  it('点击折叠行展开为完整卡：徽章与正文出现，点收起回到单行', () => {
    render(
      <ToolCard icon="terminal" title="bash" badge="0.8s" status="ok">
        uv run pytest -q
      </ToolCard>,
    )

    fireEvent.click(screen.getByRole('button', { name: /bash/ }))
    expect(screen.getByText('0.8s')).toBeTruthy()
    expect(screen.getByText('uv run pytest -q')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '收起' }))
    expect(screen.queryByText('0.8s')).toBeNull()
  })

  it('defaultExpanded 直接以完整卡呈现', () => {
    render(
      <ToolCard icon="file-frame" title="读取 styles.css" badge="9 档间距" defaultExpanded>
        --space-2 …
      </ToolCard>,
    )

    expect(screen.getByText('9 档间距')).toBeTruthy()
    expect(screen.getByText('--space-2 …')).toBeTruthy()
  })

  it('状态三态各有标记：成功勾 / 失败叉 / 运行中呼吸点', () => {
    const ok = render(<ToolCard icon="terminal" title="t" status="ok">x</ToolCard>)
    expect(screen.getByLabelText('成功')).toBeTruthy()
    ok.unmount()

    const failed = render(<ToolCard icon="terminal" title="t" status="failed">x</ToolCard>)
    expect(screen.getByLabelText('失败')).toBeTruthy()
    failed.unmount()

    render(<ToolCard icon="terminal" title="t" status="running">x</ToolCard>)
    expect(screen.getByLabelText('运行中')).toBeTruthy()
  })

  it('不同工具换不同图标（globe）', () => {
    const { container } = render(<ToolCard icon="globe" title="web_search" status="ok" />)
    expect(container.querySelector('svg')).toBeTruthy()
    expect(screen.getByText('web_search')).toBeTruthy()
  })
})
