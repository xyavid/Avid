// @vitest-environment jsdom
/**
 * 渲染成本的回归门禁（P2-8）。
 *
 * 为什么需要组件级测试：流式期间父组件每帧重渲染，而"哪些东西跟着重算"只有把组件
 * 真的渲染出来才看得见——纯函数单测覆盖不到 memo 是否生效（`reducer`/`coalescer`
 * 的用例都绿着，界面照样每帧重解析 markdown）。
 *
 * 手法：mock 掉子模块并数调用次数。`memo` 生效 ⇒ 同一个 props 引用下组件体不再执行
 * ⇒ 它调用的子模块也不该再被调用。
 */

import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const markdownCalls = vi.hoisted(() => ({ count: 0 }))
const shapeCalls = vi.hoisted(() => ({ count: 0 }))

vi.mock('../../../lib/markdown', () => ({
  Markdown: ({ text }: { text: string }) => {
    markdownCalls.count += 1
    return <span data-testid="md">{text}</span>
  },
}))

vi.mock('../../../ui/sketch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../ui/sketch')>()
  return {
    ...actual,
    shapeFor: (index: number) => {
      shapeCalls.count += 1
      return actual.shapeFor(index)
    },
  }
})

import { EntryRow } from '../EntryRow'
import type { EntryRowProps } from '../EntryRow'
import type { TimelineEntry } from '../../../lib/timeline'

const ENTRY: TimelineEntry = {
  id: 'entry-1',
  kind: 'assistant',
  text: '**一**段正文',
  seq: 3,
  at: 1_700_000_000_000,
}

function props(overrides: Partial<EntryRowProps> = {}): EntryRowProps {
  return {
    entry: ENTRY,
    shapeIndex: 1,
    density: 'comfy',
    onInspect: () => undefined,
    onCopy: () => undefined,
    onFork: () => undefined,
    ...overrides,
  }
}

beforeEach(() => {
  markdownCalls.count = 0
  shapeCalls.count = 0
})

afterEach(cleanup)

describe('EntryRow 的 memo', () => {
  it('同一份 props 重渲染时组件体不再执行（markdown 与形状计算都不重算）', () => {
    const stable = props()
    const { rerender } = render(<EntryRow {...stable} />)
    expect(markdownCalls.count).toBe(1)
    expect(shapeCalls.count).toBe(1)

    // 父组件每帧都会重渲染；props 引用不变时必须被 memo 挡住。
    for (let frame = 0; frame < 10; frame += 1) {
      rerender(<EntryRow {...stable} />)
    }

    expect(markdownCalls.count).toBe(1)
    expect(shapeCalls.count).toBe(1)
  })

  it('正文变了才重算（流式期间只有当前那条在解析 markdown）', () => {
    const first = props()
    const { rerender } = render(<EntryRow {...first} />)
    expect(markdownCalls.count).toBe(1)

    rerender(<EntryRow {...props({ entry: { ...ENTRY, text: '**一**段正文，又长了一点' } })} />)

    expect(markdownCalls.count).toBe(2)
  })

  it('回调换了身份就照常重渲染（不能挡掉真的变化）', () => {
    const { rerender } = render(<EntryRow {...props()} />)
    rerender(<EntryRow {...props({ onCopy: () => undefined })} />)

    expect(markdownCalls.count).toBe(2)
  })
})
