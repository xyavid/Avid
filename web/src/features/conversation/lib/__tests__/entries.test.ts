/**
 * 两个取数纯函数的用例：分页拍平（含"还没到"）与工具结果查表。
 *
 * 都在断言**边界**而不是快乐路径：`pages` 缺席时给空数组、工具条目与普通条目混在一起时
 * 只认 kind 为 tool 且 id 相同的那一条。
 */

import { describe, expect, it } from 'vitest'

import type { Entry } from '../../../../api/types'
import type { TimelineEntry } from '../../../../lib/timeline'
import { findToolText, flattenEntries } from '../entries'

function entry(id: string): Entry {
  return { entry_id: id, parent_id: null, seq: 1, timestamp: 1, type: 'message', message: null }
}

function timelineEntry(overrides: Partial<TimelineEntry> & Pick<TimelineEntry, 'id'>): TimelineEntry {
  return { kind: 'assistant', text: '', seq: 1, at: 1, ...overrides }
}

describe('flattenEntries：分页页面 → 平铺条目', () => {
  it('还没加载时给空数组', () => {
    expect(flattenEntries(undefined)).toEqual([])
  })

  it('空页面给空数组', () => {
    expect(flattenEntries([{ entries: [] }])).toEqual([])
  })

  it('多页按给定顺序首尾相接（不重排：顺序是服务端的事实）', () => {
    const pages = [
      { entries: [entry('a'), entry('b')] },
      { entries: [entry('c')] },
    ]
    expect(flattenEntries(pages).map((item) => item.entry_id)).toEqual(['a', 'b', 'c'])
  })
})

describe('findToolText：按 toolCallId 取工具结果正文', () => {
  const entries: TimelineEntry[] = [
    timelineEntry({ id: 'u1', kind: 'user', text: '问题' }),
    timelineEntry({ id: 't1', kind: 'tool', toolCallId: 'call_1', text: '工具输出' }),
    timelineEntry({ id: 't2', kind: 'tool', toolCallId: 'call_2', text: '另一个输出' }),
  ]

  it('命中同一次调用的那一条', () => {
    expect(findToolText(entries, 'call_2')).toBe('另一个输出')
  })

  it('id 对不上给空串（分页还没到）', () => {
    expect(findToolText(entries, 'call_404')).toBe('')
  })

  it('只认工具条目：普通条目的 toolCallId 即使相同也不作数', () => {
    const shadowed: TimelineEntry[] = [
      timelineEntry({ id: 'a1', kind: 'assistant', toolCallId: 'call_1', text: '不是工具输出' }),
    ]
    expect(findToolText(shadowed, 'call_1')).toBe('')
  })
})
