// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ToolCard } from '../ToolCard'
import type { ToolRun } from '../../../../events/reducer'

afterEach(cleanup)

function run(extra: Partial<ToolRun> = {}): ToolRun {
  return {
    toolCallId: 'c1',
    tool: 'read_file',
    args: { path: 'src/avid/cli.py' },
    status: 'ok',
    resultText: 'print(1)',
    durationMs: 42,
    startedAt: 1_700_000_000_000,
    ...extra,
  }
}

/** 头部那个折叠按钮：唯一带 aria-expanded 的按钮。 */
function toggleButton(): HTMLElement {
  const found = screen.getAllByRole('button').find((el) => el.hasAttribute('aria-expanded'))
  if (!found) throw new Error('没有找到折叠按钮')
  return found
}

describe('ToolCard', () => {
  it('running 用旋转的 LoaderIcon，且默认展开', () => {
    const { container } = render(
      <ToolCard toolRun={run({ status: 'running', durationMs: null, resultText: '跑着呢' })} />,
    )

    expect(container.querySelector('.animate-spin')).not.toBeNull()
    expect(toggleButton().getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('跑着呢')).toBeTruthy()
  })

  it('ok 用 text-ok 的勾，且默认折叠（结果不占位）', () => {
    const { container } = render(<ToolCard toolRun={run({ status: 'ok' })} />)

    expect(container.querySelector('.text-ok')).not.toBeNull()
    expect(toggleButton().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('print(1)')).toBeNull()
  })

  it('error 用 text-danger，denied 用 text-warn', () => {
    const failed = render(<ToolCard toolRun={run({ status: 'error' })} />)
    expect(failed.container.querySelector('.text-danger')).not.toBeNull()
    cleanup()

    const denied = render(<ToolCard toolRun={run({ status: 'denied' })} />)
    expect(denied.container.querySelector('.text-warn')).not.toBeNull()
  })

  it('durationMs 为 null 时耗时显示「—」而不是 0', () => {
    render(<ToolCard toolRun={run({ durationMs: null })} />)

    expect(screen.getByText('—')).toBeTruthy()
  })

  it('有耗时时按毫秒/秒两档显示，并给出参数摘要与工具名', () => {
    render(<ToolCard toolRun={run({ durationMs: 1234 })} />)

    expect(screen.getByText('read_file')).toBeTruthy()
    expect(screen.getByText('src/avid/cli.py')).toBeTruthy()
    expect(screen.getByText('1.2 s')).toBeTruthy()
  })

  it('subagent 卡显示子任务徽标，摘要位置换成子任务描述', () => {
    render(
      <ToolCard
        toolRun={run({
          tool: 'task',
          args: { path: '不该显示的参数' },
          subagent: { task: '读四个文件并汇总', index: 2 },
        })}
      />,
    )

    expect(screen.getByText('子任务 #2')).toBeTruthy()
    expect(screen.getByText('读四个文件并汇总')).toBeTruthy()
    expect(screen.queryByText('不该显示的参数')).toBeNull()
  })

  it('onInspect 回调收到的是 toolCallId', () => {
    const onInspect = vi.fn()
    render(<ToolCard toolRun={run({ toolCallId: 'call-9' })} onInspect={onInspect} />)

    fireEvent.click(screen.getByRole('button', { name: '查看 read_file 的调用详情' }))

    expect(onInspect).toHaveBeenCalledWith('call-9')
  })

  it('没有 onInspect 时不出现「查看」按钮', () => {
    render(<ToolCard toolRun={run()} />)

    expect(screen.queryByRole('button', { name: /的调用详情/ })).toBeNull()
  })

  it('点折叠按钮能展开再收起', () => {
    render(<ToolCard toolRun={run({ status: 'ok' })} />)

    fireEvent.click(toggleButton())
    expect(screen.getByText('print(1)')).toBeTruthy()

    fireEvent.click(toggleButton())
    expect(screen.queryByText('print(1)')).toBeNull()
  })

  it('用户点过之后，状态变化不再抢走折叠控制权', () => {
    const { rerender } = render(<ToolCard toolRun={run({ status: 'ok' })} />)
    fireEvent.click(toggleButton()) // 用户主动展开
    expect(screen.getByText('print(1)')).toBeTruthy()

    rerender(<ToolCard toolRun={run({ status: 'ok', resultText: 'print(2)' })} />)

    expect(screen.getByText('print(2)')).toBeTruthy()
  })
})
