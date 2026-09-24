// @vitest-environment jsdom
/**
 * 条目动作行的可用性：**谁配得上"从此处分支"**。
 *
 * 分叉的语义是"以这条消息为链尾另开一条分支"，落点必须是**模型产出**的那一侧：
 * 用户消息上的分叉既不是"编辑重发"（那要改内容），也不是"从这轮继续"（链尾在模型的
 * 回答上），点下去只会得到一条与主线共享全部历史的空分支——多一个看不出差别的选择。
 * 所以用户消息只留复制；模型回复、工具结果照旧两枚都在。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ReactNode } from 'react'

import { LocaleProvider } from '../../../lib/i18n'
import type { TimelineEntry } from '../../../lib/timeline'
import { EntryRow } from '../EntryRow'

function withLocale(node: ReactNode) {
  return render(<LocaleProvider>{node}</LocaleProvider>)
}

function entry(kind: TimelineEntry['kind'], overrides: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    id: `e-${kind}`,
    kind,
    text: '一句话',
    // 有 `entryId` 才可能成为分叉点（乐观条目还没有 id）。
    entryId: `id-${kind}`,
    seq: 1,
    at: 1_700_000_000_000,
    ...overrides,
  } as TimelineEntry
}

const COPY = '复制文本'
const FORK = '从此处分支'

afterEach(cleanup)

describe('条目动作行', () => {
  it('用户消息只有复制', () => {
    withLocale(<EntryRow entry={entry('user')} onCopy={() => undefined} onFork={() => undefined} />)
    expect(screen.getByRole('button', { name: COPY })).toBeTruthy()
    expect(screen.queryByRole('button', { name: FORK })).toBeNull()
  })

  it('模型回复两枚都在', () => {
    withLocale(
      <EntryRow entry={entry('assistant')} onCopy={() => undefined} onFork={() => undefined} />,
    )
    expect(screen.getByRole('button', { name: COPY })).toBeTruthy()
    expect(screen.getByRole('button', { name: FORK })).toBeTruthy()
  })

  it('工具结果照旧两枚都在', () => {
    withLocale(<EntryRow entry={entry('tool')} onCopy={() => undefined} onFork={() => undefined} />)
    expect(screen.getByRole('button', { name: COPY })).toBeTruthy()
    expect(screen.getByRole('button', { name: FORK })).toBeTruthy()
  })

  it('没落库的条目（乐观 delta）仍然不能分叉', () => {
    withLocale(
      <EntryRow
        entry={entry('assistant', { entryId: undefined })}
        onCopy={() => undefined}
        onFork={() => undefined}
      />,
    )
    expect(screen.queryByRole('button', { name: FORK })).toBeNull()
  })
})
