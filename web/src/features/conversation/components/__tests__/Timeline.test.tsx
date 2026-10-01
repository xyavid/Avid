// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Timeline } from '../Timeline'
import type { TimelineEntry, ToolRun } from '../../../../events/reducer'

afterEach(cleanup)

let seq = 0

function entry(kind: TimelineEntry['kind'], extra: Partial<TimelineEntry> = {}): TimelineEntry {
  seq += 1
  return { id: `e${seq}`, kind, ts: 1_700_000_000_000 + seq, text: `正文${seq}`, ...extra }
}

function toolRun(extra: Partial<ToolRun> = {}): ToolRun {
  return {
    toolCallId: 'c1',
    tool: 'read_file',
    args: { path: 'a.py' },
    status: 'ok',
    resultText: '结果',
    durationMs: 5,
    startedAt: 1,
    ...extra,
  }
}

function logEl(): HTMLElement {
  return screen.getByRole('log')
}

/**
 * 伪造滚动几何。
 *
 * jsdom 没有布局，`scrollHeight` / `clientHeight` 恒为 0，"在不在底部"就永远算成在底部。
 * 这里直接在自己身上定义这几个属性（数据属性遮蔽原型访问器），
 * 让 40px 阈值这条判定真的被走到。
 */
function pretendScroll(
  el: HTMLElement,
  geometry: { scrollHeight: number; clientHeight: number; scrollTop: number },
): void {
  Object.defineProperty(el, 'scrollHeight', { value: geometry.scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: geometry.clientHeight, configurable: true })
  Object.defineProperty(el, 'scrollTop', {
    value: geometry.scrollTop,
    writable: true,
    configurable: true,
  })
}

describe('Timeline', () => {
  it('没有条目时给一句空态文案', () => {
    render(<Timeline entries={[]} tools={[]} streaming={false} />)

    expect(screen.getByText('还没有内容')).toBeTruthy()
    expect(logEl()).toBeTruthy()
  })

  it('渲染消息正文，并把 now 传给气泡做相对时间', () => {
    const ts = 1_700_000_000_000
    render(
      <Timeline
        entries={[entry('user', { text: '读一下 pyproject', ts })]}
        tools={[]}
        streaming={false}
        now={ts + 3 * 60_000}
      />,
    )

    expect(screen.getByText('读一下 pyproject')).toBeTruthy()
    expect(screen.getByText('3 分钟前')).toBeTruthy()
  })

  it('工具块按 toolCallId 找到 ToolRun 并画成 ToolCard', () => {
    render(
      <Timeline
        entries={[entry('tool', { toolCallId: 'c1', tool: 'read_file' })]}
        tools={[toolRun({ toolCallId: 'c1', tool: 'read_file' })]}
        streaming={false}
      />,
    )

    expect(screen.getByText('read_file')).toBeTruthy()
  })

  it('找不到 ToolRun 时降级成一条提示，而不是把这块静默吞掉', () => {
    render(
      <Timeline
        entries={[entry('tool', { toolCallId: 'gone', tool: 'read_file' })]}
        tools={[]}
        streaming={false}
      />,
    )

    expect(screen.getByText(/调用记录不在本次重放窗口内/)).toBeTruthy()
  })

  it('初始在底部，不给「回到底部」按钮', () => {
    render(<Timeline entries={[entry('assistant')]} tools={[]} streaming={false} />)

    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
  })

  it('距底不足 40px 仍算在底部（阈值不是 0，这是子像素余量）', () => {
    render(<Timeline entries={[entry('assistant')]} tools={[]} streaming={false} />)
    const el = logEl()
    pretendScroll(el, { scrollHeight: 1000, clientHeight: 400, scrollTop: 561 }) // 差 39

    fireEvent.scroll(el)

    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
  })

  it('用户上滚超过 40px 后停止跟随，并出现「回到底部」', () => {
    render(<Timeline entries={[entry('assistant')]} tools={[]} streaming />)
    const el = logEl()
    pretendScroll(el, { scrollHeight: 1000, clientHeight: 400, scrollTop: 500 }) // 差 100

    fireEvent.scroll(el)

    expect(screen.getByRole('button', { name: '回到底部' })).toBeTruthy()
  })

  it('用户上滚后即使还在流式也不再自动贴底', () => {
    const { rerender } = render(
      <Timeline entries={[entry('assistant', { text: 'a' })]} tools={[]} streaming />,
    )
    const el = logEl()
    pretendScroll(el, { scrollHeight: 2000, clientHeight: 400, scrollTop: 100 })
    fireEvent.scroll(el)
    expect(el.scrollTop).toBe(100)

    // 流式又追加了一段：读者正在看旧内容，不能被拽回末尾。
    rerender(
      <Timeline
        entries={[entry('assistant', { text: 'a' }), entry('assistant', { text: 'b' })]}
        tools={[]}
        streaming
      />,
    )

    expect(el.scrollTop).toBe(100)
  })

  it('流式中且用户在底部时持续贴底', () => {
    const { rerender } = render(
      <Timeline entries={[entry('assistant', { text: 'a' })]} tools={[]} streaming />,
    )
    const el = logEl()
    pretendScroll(el, { scrollHeight: 2000, clientHeight: 400, scrollTop: 0 })

    rerender(
      <Timeline
        entries={[entry('assistant', { text: 'a' }), entry('assistant', { text: 'b' })]}
        tools={[]}
        streaming
      />,
    )

    expect(el.scrollTop).toBe(2000)
  })

  it('点「回到底部」后回到贴底状态，按钮消失', () => {
    render(<Timeline entries={[entry('assistant')]} tools={[]} streaming />)
    const el = logEl()
    pretendScroll(el, { scrollHeight: 1500, clientHeight: 400, scrollTop: 0 })
    fireEvent.scroll(el)
    expect(screen.getByRole('button', { name: '回到底部' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '回到底部' }))

    expect(el.scrollTop).toBe(1500)
    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
  })
})
