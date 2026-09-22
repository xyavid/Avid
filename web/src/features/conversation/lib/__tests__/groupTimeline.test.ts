/**
 * 时间线编织的两条规则（纯函数，脱离 DOM 断言）：
 *
 *   1. 只声明工具调用、正文为空的 assistant 回合**不占一张卡**——那张卡除了角色名
 *      什么都没有，这次调用由工具卡承担。
 *   2. 它必须仍然留在 `entries` 里：工具卡是挂到「声明它的那个条目」上的，条目一删，
 *      工具卡就掉到时间线末尾。所以这里既断言「没有卡片」，也断言「工具卡在原位」。
 */

import { describe, expect, it } from 'vitest'

import type { TimelineEntry, ToolRun } from '../../../../lib/timeline'
import { groupTimeline } from '../groupTimeline'

const readRun: ToolRun = {
  toolCallId: 'call-1',
  tool: 'read_file',
  arguments: { path: 'a.py' },
  status: 'ok',
  truncated: false,
  contentChars: 3,
  durationMs: 5,
  seq: 3,
  at: 3,
}

function entry(partial: Partial<TimelineEntry> & { id: string }): TimelineEntry {
  return { kind: 'assistant', text: '', seq: 1, at: 1, ...partial }
}

describe('groupTimeline：空回合不占卡片，但工具卡留在原位', () => {
  it('正文为空的 assistant 回合被跳过，它声明的工具调用仍在被跳过的位置', () => {
    const blocks = groupTimeline(
      [
        entry({ id: 'user', kind: 'user', text: '问题', seq: 1 }),
        entry({
          id: 'silent',
          text: '',
          seq: 2,
          toolCalls: [{ toolCallId: 'call-1', tool: 'read_file', arguments: {} }],
        }),
        entry({ id: 'answer', text: '答案', seq: 4 }),
      ],
      [readRun],
    )

    // 关键：tool 卡夹在两条消息之间，而不是被兜底逻辑甩到末尾
    expect(blocks.map((block) => block.kind)).toEqual(['entry', 'tool', 'entry'])
    expect(blocks.map((block) => block.id)).toEqual(['user', 'tool:call-1', 'answer'])
  })

  it('连续多个空回合不打断可见卡片的顺序（空回合自身不占位）', () => {
    const blocks = groupTimeline(
      [
        entry({ id: 'user', kind: 'user', text: '问题' }),
        entry({ id: 'silent-1', text: '' }),
        entry({ id: 'silent-2', text: '' }),
        entry({ id: 'answer', text: '答案' }),
      ],
      [],
    )

    // 空回合既不占卡也不插空位：可见卡片就是 ['user', 'answer']，中间不留缝。
    expect(blocks.map((block) => block.id)).toEqual(['user', 'answer'])
  })

  it('有正文的 assistant 回合照旧占一张卡', () => {
    const blocks = groupTimeline([entry({ id: 'answer', text: '答案' })], [])

    expect(blocks).toHaveLength(1)
    expect(blocks[0]).toMatchObject({ kind: 'entry', id: 'answer' })
  })
})
