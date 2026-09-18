/**
 * B8 / I11 / I12 的回归：durable 与 delta 的分工、幂等、重建。
 *
 * 这些用例不碰 DOM，只喂事件——`applyEvent` 是纯函数，收敛性可以脱离 UI 断言。
 */

import { describe, expect, it } from 'vitest'

import { applyDelta, applyEvent, emptyView, viewFromEntries } from '../reducer'
import type { RunView } from '../reducer'
import type { AvidEventType, EventData, EventEnvelope } from '../types'
import type { Entry } from '../../api/types'

const RUN = 'run-1'
const SESSION = 'session-1'

function ev(type: AvidEventType, seq: number | null, data: EventData = {}): EventEnvelope {
  return { run_id: RUN, session_id: SESSION, seq, ts: 1_000 + (seq ?? 0), type, data }
}

/** 时间线的可比形状：kind + 文本（不看 id/时间戳，那两样不是收敛条件）。 */
function timeline(view: RunView): string[] {
  return view.entries.map((entry) => `${entry.kind}:${entry.text}`)
}

const started = ev('run_started', 1)
const userMessage = ev('user_message', 2, {
  entry_id: 'entry-user',
  message: { role: 'user', content: '问题' },
})
const assistantMessage = ev('assistant_message', 3, {
  entry_id: 'entry-assistant',
  message: { role: 'assistant', content: '回答' },
})
const finished = ev('run_finished', 4, { tokens: 42 })

const deltaOne = ev('assistant_delta', null, { text: '回' })
const deltaTwo = ev('assistant_delta', null, { text: '答' })
const deltaLate = ev('assistant_delta', null, { text: '（迟到的残片）' })

function fold(events: EventEnvelope[], seed: RunView = emptyView(SESSION)): RunView {
  return events.reduce((view, event) => applyEvent(view, event), seed)
}

describe('applyEvent：durable 是权威，delta 是易失的', () => {
  it('只喂 durable 的最终状态 == 混入任意 delta（中途被打断）的最终状态', () => {
    const durableOnly = fold([started, userMessage, assistantMessage, finished])

    const mixed = fold([started, userMessage, deltaOne, deltaTwo, assistantMessage, deltaLate, finished])

    // 最后一条 delta 落在 assistant_message 之后：它不能残留，也不能造成重复文本。
    expect(timeline(mixed)).toEqual(timeline(durableOnly))
    expect(mixed.phase).toBe(durableOnly.phase)
    expect(mixed.seq).toBe(durableOnly.seq)
    expect(mixed.deltaText).toBe('')
    expect(timeline(durableOnly)).toEqual(['user:问题', 'assistant:回答'])
  })

  it('delta 流在任意点被切断，durable 补齐后文本不重复（渲染前 flush）', () => {
    let interrupted = fold([started, userMessage, deltaOne, deltaTwo])
    // 切断时只有 deltaText 与已落地的 user 条目，没有乐观的 assistant 条目。
    expect(interrupted.deltaText).toBe('回答')
    expect(interrupted.entries).toHaveLength(1)
    expect(interrupted.entries.filter((entry) => entry.optimistic)).toHaveLength(0)

    interrupted = applyEvent(interrupted, assistantMessage)
    interrupted = applyEvent(interrupted, finished)

    expect(timeline(interrupted)).toEqual(['user:问题', 'assistant:回答'])
    expect(interrupted.phase).toBe('done')
    expect(interrupted.deltaText).toBe('')
  })

  it('(run_id, seq) 幂等：同一事件应用两次，第二次返回同一引用', () => {
    const view = fold([started, userMessage, assistantMessage])

    const replayed = applyEvent(view, assistantMessage)

    expect(replayed).toBe(view)
    expect(replayed.seq).toBe(3)
    expect(timeline(replayed)).toEqual(['user:问题', 'assistant:回答'])

    // 更旧的 durable 事件（seq 2）同样不改变状态。
    expect(applyEvent(view, userMessage)).toBe(view)
  })

  it('durable 渲染前 flush：乐观条目被 durable 条目就地替换', () => {
    let view = applyEvent(emptyView(SESSION), started)
    view = applyDelta(view, '半句')
    expect(view.entries).toHaveLength(0)
    expect(view.deltaText).toBe('半句')

    view = applyEvent(
      view,
      ev('assistant_message', 2, {
        entry_id: 'entry-assistant',
        message: { role: 'assistant', content: '半句' },
      }),
    )

    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]?.text).toBe('半句')
    expect(view.entries[0]?.optimistic).toBeFalsy()
    expect(view.deltaText).toBe('')
  })

  it('终止事件渲染前 cancel：乐观条目消失且 deltaText 清空', () => {
    let view = applyEvent(emptyView(SESSION), started)
    view = applyDelta(view, '不该提交的半句')

    view = applyEvent(view, ev('run_cancelled', 2, { reason: 'user' }))

    expect(view.phase).toBe('cancelled')
    expect(view.deltaText).toBe('')
    expect(view.entries.filter((entry) => entry.optimistic)).toHaveLength(0)
    expect(view.entries).toHaveLength(0)
  })
})

describe('viewFromEntries：条目是权威视图', () => {
  it('按 seq 重建时间线并清掉 detached', () => {
    const entries: Entry[] = [
      {
        entry_id: 'entry-b',
        parent_id: null,
        seq: 2,
        timestamp: 200,
        type: 'message',
        message: { role: 'assistant', content: '回答' },
      },
      {
        entry_id: 'entry-a',
        parent_id: null,
        seq: 1,
        timestamp: 100,
        type: 'message',
        message: { role: 'user', content: '问题' },
      },
      // 非 message 条目不进时间线。
      { entry_id: 'entry-c', parent_id: null, seq: 3, timestamp: 300, type: 'notice', message: null },
    ]

    const detached = applyEvent(emptyView(SESSION), ev('resync', 9))
    expect(detached.detached).toBe(true)

    const rebuilt = viewFromEntries(detached, entries)

    expect(rebuilt.detached).toBe(false)
    expect(rebuilt.entries.map((entry) => entry.id)).toEqual(['entry-a', 'entry-b'])
    expect(timeline(rebuilt)).toEqual(['user:问题', 'assistant:回答'])
  })
})

describe('工具调用收敛', () => {
  it('tool_call_finished 更新同一个 ToolRun，status 从 running 变 failed', () => {
    let view = applyEvent(
      emptyView(SESSION),
      ev('tool_call_started', 1, { tool_call_id: 'call-1', tool: 'bash', arguments: { command: 'ls' } }),
    )
    expect(view.tools).toHaveLength(1)
    expect(view.tools[0]?.status).toBe('running')

    view = applyEvent(
      view,
      ev('tool_call_finished', 2, {
        tool_call_id: 'call-1',
        tool: 'bash',
        status: 'failed',
        content_chars: 12,
        duration_ms: 340,
        truncated: true,
      }),
    )

    expect(view.tools).toHaveLength(1)
    expect(view.tools[0]?.toolCallId).toBe('call-1')
    expect(view.tools[0]?.status).toBe('failed')
    expect(view.tools[0]?.contentChars).toBe(12)
    expect(view.tools[0]?.durationMs).toBe(340)
    expect(view.tools[0]?.truncated).toBe(true)
  })
})
