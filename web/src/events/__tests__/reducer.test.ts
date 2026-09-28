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

const reasoning = ev('reasoning_delta', null, { text: '先看目录。' })

describe('A2：思维链增量只进「思考中」卡片', () => {
  it('累加但不合成条目——它不是回复正文', () => {
    const view = fold([started, reasoning, ev('reasoning_delta', null, { text: '再读文件。' })])

    expect(view.thinkingText).toBe('先看目录。再读文件。')
    expect(view.entries.filter((entry) => entry.kind === 'assistant')).toHaveLength(0)
    expect(view.entries.some((entry) => entry.optimistic)).toBe(false)
  })

  it('回答落地或运行收尾后不留残片（否则会串到下一轮）', () => {
    expect(fold([started, reasoning, assistantMessage]).thinkingText).toBe('')
    expect(fold([started, reasoning, finished]).thinkingText).toBe('')
    expect(fold([started, reasoning, ev('run_failed', 5, { code: 'x' })]).thinkingText).toBe('')
  })

  it('新一轮开始时也清掉上一轮的思维链', () => {
    const view = fold([started, reasoning, ev('run_started', 6)])

    expect(view.thinkingText).toBe('')
  })
})

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
    // 切断时乐观条目已带全部增量（delta 必须当帧可见），durable 条目尚未到达。
    expect(interrupted.deltaText).toBe('')
    expect(interrupted.entries).toHaveLength(2)
    expect(interrupted.entries[1]?.text).toBe('回答')
    expect(interrupted.entries[1]?.optimistic).toBe(true)

    interrupted = applyEvent(interrupted, assistantMessage)
    interrupted = applyEvent(interrupted, finished)

    expect(timeline(interrupted)).toEqual(['user:问题', 'assistant:回答'])
    expect(interrupted.phase).toBe('done')
    expect(interrupted.deltaText).toBe('')
    expect(interrupted.entries.filter((entry) => entry.optimistic)).toHaveLength(0)
  })

  it('每个 delta 到达时都可见：乐观条目逐次增长，不等 durable', () => {
    let view = applyEvent(emptyView(SESSION), started)

    view = applyDelta(view, '你')
    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]?.text).toBe('你')
    expect(view.entries[0]?.optimistic).toBe(true)

    view = applyDelta(view, '好')
    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]?.text).toBe('你好')

    // 空 delta 不产生新引用（合并器每帧都可能调一次）。
    expect(applyDelta(view, '')).toBe(view)
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
    // delta 已当帧可见（乐观条目），durable 到达后就地替换它。
    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]?.optimistic).toBe(true)
    expect(view.entries[0]?.text).toBe('半句')
    expect(view.deltaText).toBe('')

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
      // 内核注入的提醒：role 是 user，但类型不是 message，所以不进时间线。
      {
        entry_id: 'entry-reminder',
        parent_id: null,
        seq: 3,
        timestamp: 300,
        type: 'notice',
        message: { role: 'user', content: '[提醒] 该更新计划了' },
      },
      // 没有 message 的条目同样跳过。
      { entry_id: 'entry-c', parent_id: null, seq: 4, timestamp: 400, type: 'notice', message: null },
    ]

    const detached = applyEvent(emptyView(SESSION), ev('resync', 9))
    expect(detached.detached).toBe(true)

    const rebuilt = viewFromEntries(detached, entries)

    expect(rebuilt.detached).toBe(false)
    expect(rebuilt.entries.map((entry) => entry.id)).toEqual(['entry-a', 'entry-b'])
    expect(timeline(rebuilt)).toEqual(['user:问题', 'assistant:回答'])
  })

  it('注入的提醒即使只有事件、没有条目，也不画进时间线', () => {
    // 事件仍在流里（可观察、可回放），但时间线只放「用户输入 / Avid 的回答 / 工具调用」。
    const view = applyEvent(
      applyEvent(emptyView(SESSION), userMessage),
      ev('todo_reminder', 7, { content: '[提醒] 该更新计划了', entry_id: 'entry-reminder' }),
    )
    const nudged = applyEvent(view, ev('stop_nudge', 8, { content: '还有一步' }))

    expect(timeline(nudged)).toEqual(['user:问题'])
    // seq 照常推进：提醒仍是 durable 事件，游标不能停在它前面。
    expect(nudged.seq).toBe(8)
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

describe('usage 快照收敛（阶段 22）', () => {
  const snapshot = {
    context: {
      tokens: 72_000,
      window: 200_000,
      utilization: 0.36,
      parts: { system: 2_000, tools: 6_000, messages: 64_000 },
    },
    cache: { read_tokens: 56_000, write_tokens: null, hit_ratio: 0.778 },
    compaction: { count: 2, last_compaction_tokens: 42_000, last_step: 'micro_compact' },
  }

  it('run_status 每轮刷新快照，run_finished 补最终值', () => {
    const first = applyEvent(emptyView(SESSION), ev('run_status', null, { usage: snapshot }))
    expect(first.usage).toEqual(snapshot)

    const later = applyEvent(
      first,
      ev('run_status', null, {
        usage: {
          ...snapshot,
          context: {
            tokens: 90_000,
            window: 200_000,
            utilization: 0.45,
            parts: { system: 2_000, tools: 8_000, messages: 80_000 },
          },
        },
      }),
    )
    expect(later.usage?.context.tokens).toBe(90_000)

    const done = applyEvent(later, ev('run_finished', 4, { usage: snapshot }))
    expect(done.usage).toEqual(snapshot)
  })

  it('状态事件不带 usage 时保留上一份，不抹成空', () => {
    const withUsage = applyEvent(emptyView(SESSION), ev('run_status', null, { usage: snapshot }))
    const withoutUsage = applyEvent(withUsage, ev('run_status', null, { round: 2, tokens: 5 }))
    expect(withoutUsage.usage).toEqual(snapshot)
  })

  it('全空视图的 usage 是 null：界面据此回落查询域（落盘值）或显示「—」', () => {
    expect(emptyView(SESSION).usage).toBeNull()
  })

  it('用条目重建视图不会清掉实时快照（resync 期间读数要连续）', () => {
    const withUsage = applyEvent(emptyView(SESSION), ev('run_status', null, { usage: snapshot }))
    const rebuilt = viewFromEntries(withUsage, [])
    expect(rebuilt.usage).toEqual(snapshot)
  })
})
