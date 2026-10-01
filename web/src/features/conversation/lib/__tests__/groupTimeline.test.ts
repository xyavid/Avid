import { describe, expect, it } from 'vitest'

import { blockToolCallId, groupTimeline } from '../groupTimeline'
import type { TimelineBlock } from '../groupTimeline'
import type { TimelineEntry } from '../../../../events/reducer'

let seq = 0

/** 造一条条目；`extra` 覆盖默认字段（含 id），让用例只写它关心的那部分。 */
function entry(kind: TimelineEntry['kind'], extra: Partial<TimelineEntry> = {}): TimelineEntry {
  seq += 1
  return { id: `e${seq}`, kind, ts: 1_700_000_000_000 + seq, text: `t${seq}`, ...extra }
}

/** 把块摊回条目序列——守恒不变量就是拿它跟输入比。 */
function flatten(blocks: readonly TimelineBlock[]): TimelineEntry[] {
  return blocks.flatMap((block) => block.entries)
}

function idList(entries: readonly TimelineEntry[]): string[] {
  return entries.map((item) => item.id).sort()
}

describe('groupTimeline', () => {
  it('空输入返回空数组', () => {
    expect(groupTimeline([])).toEqual([])
  })

  it('相邻 assistant 合并成一段', () => {
    const parts = [entry('assistant'), entry('assistant'), entry('assistant')]
    const blocks = groupTimeline(parts)

    expect(blocks).toHaveLength(1)
    expect(blocks[0]?.kind).toBe('assistant')
    expect(blocks[0]?.entries).toHaveLength(3)
    expect(blocks[0]?.key).toBe(`assistant:${parts[0]?.id}`)
  })

  it('相邻 user 合并成一块（同一轮连发）', () => {
    const blocks = groupTimeline([entry('user'), entry('user')])

    expect(blocks).toHaveLength(1)
    expect(blocks[0]?.kind).toBe('user')
    expect(blocks[0]?.entries).toHaveLength(2)
  })

  it('中间隔了别的类就不合并', () => {
    const blocks = groupTimeline([
      entry('assistant'),
      entry('tool', { toolCallId: 'c1' }),
      entry('assistant'),
    ])

    expect(blocks.map((block) => block.kind)).toEqual(['assistant', 'tool', 'assistant'])
  })

  it('相邻 notice 各自成块：notice 不并入相邻组', () => {
    const blocks = groupTimeline([entry('notice'), entry('notice')])

    expect(blocks).toHaveLength(2)
    expect(blocks[0]?.entries).toHaveLength(1)
    expect(blocks[1]?.entries).toHaveLength(1)
  })

  it('同一 toolCallId 的相邻 started/finished 合成一卡', () => {
    const blocks = groupTimeline([
      entry('tool', { toolCallId: 'c1', status: 'running' }),
      entry('tool', { toolCallId: 'c1', status: 'ok' }),
    ])

    expect(blocks).toHaveLength(1)
    expect(blockToolCallId(blocks[0] as TimelineBlock)).toBe('c1')
    expect(blocks[0]?.entries).toHaveLength(2)
  })

  it('同一个 toolCallId 但不相邻时不跨段合回', () => {
    const blocks = groupTimeline([
      entry('tool', { toolCallId: 'c1' }),
      entry('assistant'),
      entry('tool', { toolCallId: 'c1' }),
    ])

    expect(blocks.map((block) => block.kind)).toEqual(['tool', 'assistant', 'tool'])
  })

  it('相邻但 toolCallId 不同的两条工具条目各自成卡', () => {
    // 合并会让第二张卡在界面上消失（一个 tool 块只渲染一张卡），所以宁可不合并。
    const blocks = groupTimeline([
      entry('tool', { toolCallId: 'c1' }),
      entry('tool', { toolCallId: 'c2' }),
    ])

    expect(blocks).toHaveLength(2)
    expect(blocks.map((block) => blockToolCallId(block))).toEqual(['c1', 'c2'])
  })

  it('没有 toolCallId 的工具条目各自成块，blockToolCallId 返回 null', () => {
    const blocks = groupTimeline([entry('tool'), entry('tool')])

    expect(blocks).toHaveLength(2)
    expect(blockToolCallId(blocks[0] as TimelineBlock)).toBeNull()
  })

  it('blockToolCallId 对非 tool 块返回 null', () => {
    const blocks = groupTimeline([entry('user'), entry('assistant'), entry('notice')])

    expect(blocks.map((block) => blockToolCallId(block))).toEqual([null, null, null])
  })

  it('条目守恒：每个输入条目恰好出现在一个块里（不吞、不复制）', () => {
    const input = [
      entry('user'),
      entry('user'),
      entry('assistant'),
      entry('tool', { toolCallId: 'c1', status: 'running' }),
      entry('tool', { toolCallId: 'c1', status: 'ok' }),
      entry('notice', { notice: 'compaction' }),
      entry('assistant'),
      entry('tool', { toolCallId: 'c2' }),
      entry('notice', { notice: 'todo' }),
      entry('user'),
    ]

    const blocks = groupTimeline(input)
    const output = flatten(blocks)

    // 多重集相等：先比条数（防复制），再比排序后的 id 列表（防吞掉与错位）。
    expect(output).toHaveLength(input.length)
    expect(idList(output)).toEqual(idList(input))
    // 每个块都非空，否则"守恒"可以靠空块凑数。
    expect(blocks.every((block) => block.entries.length > 0)).toBe(true)
  })

  it('块 key 两两不同（React 列表不能撞 key）', () => {
    const blocks = groupTimeline([
      entry('user'),
      entry('assistant'),
      entry('notice'),
      entry('tool', { toolCallId: 'c1' }),
      entry('user'),
    ])
    const keys = blocks.map((block) => block.key)

    expect(new Set(keys).size).toBe(keys.length)
  })
})
