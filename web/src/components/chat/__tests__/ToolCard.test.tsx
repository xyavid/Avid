// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ToolCard } from '../ToolCard'

afterEach(cleanup)

describe('ToolCard（双形态：折叠单行 ↔ 展开完整卡）', () => {
  it('折叠单行：动作 + 目标 + 耗时 + 状态，不渲染正文', () => {
    render(
      <ToolCard icon="terminal" verb="执行" target="uv run pytest -q" status="ok" durationMs={8400}>
        长输出内容
      </ToolCard>,
    )

    expect(screen.getByText('执行')).toBeTruthy()
    expect(screen.getByText('uv run pytest -q')).toBeTruthy()
    expect(screen.getByText('8.4s')).toBeTruthy()
    expect(screen.getByLabelText('成功')).toBeTruthy()
    expect(screen.queryByText('长输出内容')).toBeNull()
  })

  it('点开成完整卡：正文出现，点收起回到单行', () => {
    render(
      <ToolCard icon="terminal" verb="执行" target="uv run pytest -q" status="ok">
        271 passed
      </ToolCard>,
    )

    expect(screen.queryByText('271 passed')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /执行/ }))
    expect(screen.getByText('271 passed')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '收起' }))
    expect(screen.queryByText('271 passed')).toBeNull()
  })

  it('defaultExpanded 直接以完整卡呈现', () => {
    render(
      <ToolCard icon="file-frame" verb="读取" target="styles/tokens.css" defaultExpanded>
        --space-2 …
      </ToolCard>,
    )

    expect(screen.getByText('styles/tokens.css')).toBeTruthy()
    expect(screen.getByText('--space-2 …')).toBeTruthy()
  })

  it('状态三态各有标记：成功勾 / 失败叉 / 运行中呼吸点', () => {
    const ok = render(<ToolCard icon="terminal" verb="t" status="ok">x</ToolCard>)
    expect(screen.getByLabelText('成功')).toBeTruthy()
    ok.unmount()

    const failed = render(<ToolCard icon="terminal" verb="t" status="failed">x</ToolCard>)
    expect(screen.getByLabelText('失败')).toBeTruthy()
    failed.unmount()

    render(<ToolCard icon="terminal" verb="t" status="running">x</ToolCard>)
    expect(screen.getByLabelText('运行中')).toBeTruthy()
  })

  it('没有目标时只剩动作词（不摆一个空栏位）', () => {
    const { container } = render(<ToolCard icon="globe" verb="mcp__fs__stat" status="ok" />)

    expect(container.querySelector('svg')).toBeTruthy()
    expect(screen.getByText('mcp__fs__stat')).toBeTruthy()
  })

  it('子步骤按任务分组列出（展开态），各自带自己的动作与状态', () => {
    render(
      <ToolCard
        icon="git-branch"
        verb="子智能体"
        target="2 个子任务"
        status="ok"
        workspaceRoot="/w"
        defaultExpanded
        steps={[
          { task: '前端时间线', callId: 'k1', name: 'edit_file', args: '{"path":"/w/a.tsx"}', status: 'ok' },
          { task: '内核核对', callId: 'k2', name: 'read_file', args: '{"path":"/w/b.py"}', status: 'running' },
          { task: '前端时间线', callId: 'k3', name: 'bash', args: '{"command":"pnpm verify"}', status: 'failed' },
        ]}
      >
        汇总
      </ToolCard>,
    )

    expect(screen.getByText('前端时间线')).toBeTruthy()
    expect(screen.getByText('内核核对')).toBeTruthy()
    expect(screen.getByText('a.tsx')).toBeTruthy()
    expect(screen.getByText('b.py')).toBeTruthy()
    expect(screen.getByText('pnpm verify')).toBeTruthy()
    expect(screen.getByLabelText('失败')).toBeTruthy()
    expect(screen.getAllByLabelText('运行中')).toHaveLength(1)
  })
})
