/**
 * 事件折叠状态机的隔离测试：`applyEvent` 是纯函数，收敛性可以脱离 UI 断言。
 *
 * ## 先列的失败清单（AGENTS.md §6：隔离测试先把"它可能怎么坏"列全，再写实现）
 *
 *   F1  delta 累积到"当前流式条目"而不是每条新增一条：否则纯文本回答期间条目数量随
 *       token 数线性增长（200 条 delta → 200 个条目）。
 *   F2  终态事件（run_finished / run_failed / run_cancelled）渲染前必须 flush：不做的话
 *       流式正文会永远带着 `streaming` 标记，并且界面误以为还在生成（不变量 I12）。
 *   F3  durable 消息重放幂等：同一 `entry_id` 到达两次不得产生两条条目——SSE 重连会把
 *       durable 事件整段重发。
 *   F4  `tool_call_finished` 必须把 `duration_ms` 与结果状态落进 ToolRun，并且按
 *       `tool_call_id` 合并（不是新增一条孤立的运行）。
 *   F5  `approval_requested` 重放不得叠出两条待决；`approval_resolved` 按 `approval_id`
 *       收敛，且 phase 回到 running。
 *   F6  `run_failed` 必须填 `error.code` / `error.message`：失败不能只体现在 phase 上。
 *   F7  `viewFromEntries` 从落盘条目重建后与事件路径同形（刷新 / resync 后不能变成另一个界面）。
 *   F8  `applyEvent` 不修改入参（纯函数），非 delta 事件返回新引用。
 *   F9  `reasoning_delta` 单独成条目，且排在正文之前（不能并进正文）。
 *   F10 `context_compacted` 既进 `compactions` 又推 `notice` 条目，重放不重复。
 *   F11 `run_status` 更新 round/tokens/usage/activity；带 `subagent` 的状态不改父运行读数。
 *   F12 durable 消息到达后流式条目被它取代：durable 带完整正文，留流式残片会显示两遍。
 *   F13 `resync` 置 `detached = true`（补齐由上层拉条目完成）。
 *   F14 未知事件类型不抛错、不白屏（前向兼容），也不假装处理过。
 */

import { describe, expect, it } from 'vitest'

import { applyEvent, emptyView, flushDelta, viewFromEntries } from '../reducer'
import type { RunView, TimelineEntry, ToolRun } from '../reducer'
import type { AvidEventType, EventData, EventEnvelope } from '../types'
import type { Entry, UsageReport } from '../../api/types'

const RUN = 'run-1'
const SESSION = 'session-1'

function ev(type: AvidEventType, seq: number | null, data: EventData = {}): EventEnvelope {
  return { run_id: RUN, session_id: SESSION, seq, ts: 1_000 + (seq ?? 0), type, data }
}

/**
 * `run_failed` 的线上 `message` 是字符串（`svc/runs.py` 的 `_fail(record, code, message)`），
 * 而契约种子里的 `EventData.message` 是 `MessagePayload`——那是给 user_message /
 * assistant_message 用的。种子不能动，所以这里按线格式显式断言。
 */
function failedEvent(seq: number, data: { code?: string; message?: string }): EventEnvelope {
  return ev('run_failed', seq, data as unknown as EventData)
}

function fold(events: EventEnvelope[], seed: RunView = emptyView(SESSION)): RunView {
  return events.reduce((view, event) => applyEvent(view, event), seed)
}

const started = ev('run_started', 1, { prompt: '问题' })
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
const reasoningOne = ev('reasoning_delta', null, { text: '先看目录。' })
const reasoningTwo = ev('reasoning_delta', null, { text: '再读文件。' })

const USAGE: UsageReport = {
  context: {
    tokens: 1_234,
    window: 128_000,
    utilization: 0.01,
    parts: { system: 1, tools: 2, messages: 3 },
  },
  cache: { read_tokens: null, write_tokens: null, hit_ratio: null },
  compaction: { count: 0, last_compaction_tokens: null, last_step: null },
}

/** 时间线的可比形状：id + kind + 文本（幂等与重建都以这三个为准）。 */
function shape(entries: readonly TimelineEntry[]): Array<[string, string, string]> {
  return entries.map((entry) => [entry.id, entry.kind, entry.text])
}

/**
 * 工具卡的可比形状：不含时间戳与耗时——事件带这两个（`ts` / `duration_ms`），
 * 而条目落盘时不记耗时，所以"同形"只对语义字段成立。逐字段显式投影而不是解构丢弃，
 * 是为了不依赖 TS 对 rest 兄弟变量的宽松处理。
 */
function toolShape(tools: readonly ToolRun[]) {
  return tools.map((tool) => ({
    toolCallId: tool.toolCallId,
    tool: tool.tool,
    args: tool.args,
    status: tool.status,
    resultText: tool.resultText,
    subagent: tool.subagent,
  }))
}

/** 时间线条目的可比形状：同样剥掉时间戳与耗时。 */
function entryShape(entries: readonly TimelineEntry[]) {
  return entries.map((entry) => ({
    id: entry.id,
    kind: entry.kind,
    text: entry.text,
    tool: entry.tool,
    toolCallId: entry.toolCallId,
    status: entry.status,
  }))
}

describe('F1 / F2 / F12：delta 累积、终态 flush、durable 取代', () => {
  it('F1：delta 累积进同一个流式条目，而不是每条一条', () => {
    const view = fold([started, deltaOne, deltaTwo])

    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]).toMatchObject({ kind: 'assistant', text: '回答', streaming: true })
    expect(view.phase).toBe('running')
  })

  it('F2：run_finished 之前 flush，条目不再是流式且文本不丢', () => {
    const view = fold([started, deltaOne, deltaTwo, finished])

    expect(view.phase).toBe('finished')
    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]!.text).toBe('回答')
    expect(view.entries[0]!.streaming).toBeFalsy()
    expect(view.tokens).toBe(42)
  })

  it('F2：run_failed / run_cancelled 同样 flush', () => {
    const failed = fold([started, deltaOne, failedEvent(3, { code: 'x', message: '炸了' })])
    expect(failed.phase).toBe('failed')
    expect(failed.entries[0]!.streaming).toBeFalsy()
    expect(failed.entries[0]!.text).toBe('回')

    const cancelled = fold([started, deltaOne, ev('run_cancelled', 4, { reason: '用户取消' })])
    expect(cancelled.phase).toBe('cancelled')
    expect(cancelled.entries[0]!.streaming).toBeFalsy()
  })

  it('F2：flushDelta 幂等——没有流式条目时返回原引用', () => {
    const done = fold([started, deltaOne, finished])
    expect(flushDelta(done)).toBe(done)

    const streaming = fold([started, deltaOne])
    const flushed = flushDelta(streaming)
    expect(flushed).not.toBe(streaming)
    expect(flushed.entries[0]!.streaming).toBeFalsy()
  })

  it('F12：durable 的 assistant_message 取代流式残片，正文不显示两遍', () => {
    const view = fold([started, deltaOne, deltaTwo, assistantMessage])

    expect(shape(view.entries)).toEqual([['entry-assistant', 'assistant', '回答']])
  })

  it('F12：assistant_message 之后的新 delta 是新一轮，不并进上一条', () => {
    const view = fold([started, deltaOne, assistantMessage, ev('assistant_delta', null, { text: '补充' })])

    expect(shape(view.entries)).toEqual([
      ['entry-assistant', 'assistant', '回答'],
      [view.entries[1]!.id, 'assistant', '补充'],
    ])
    expect(view.entries[1]!.streaming).toBe(true)
  })
})

describe('F3 / F8：幂等与纯函数', () => {
  it('F3：整段 durable 重放（SSE 重连会把它们再发一遍）收敛到同一视图', () => {
    const events = [started, userMessage, assistantMessage, finished]
    expect(fold([...events, ...events])).toEqual(fold(events))
  })

  it('F3：同一个 entry_id 换了 seq 再来一次也不得产生第二条', () => {
    const once = fold([started, userMessage])
    const again = applyEvent(once, { ...userMessage, seq: 9 })

    expect(shape(again.entries)).toEqual([['entry-user', 'user', '问题']])
    expect(again.seq).toBe(9)
  })

  it('F3：tool_result_message 重放不产生第二条工具条目', () => {
    const events = [
      started,
      ev('tool_call_started', 2, { tool: 'read_file', tool_call_id: 'call-1', arguments: { path: 'a' } }),
      ev('tool_result_message', 3, {
        entry_id: 'entry-tool',
        message: { role: 'tool', content: '内容', tool_call_id: 'call-1' },
      }),
    ]
    const once = fold(events)

    // 工具结果挂在那张已声明的工具卡上，不另起一条同内容条目（否则界面上出现两遍）。
    expect(shape(once.entries)).toEqual([['tool:call-1', 'tool', '内容']])
    expect(fold([...events, ...events])).toEqual(once)
  })

  it('F8：不修改入参，且返回新引用', () => {
    const view = fold([started])
    const snapshot = structuredClone(view)
    const next = applyEvent(view, userMessage)

    expect(view).toEqual(snapshot)
    expect(next).not.toBe(view)
  })
})

describe('F9：思维链增量单独成条目', () => {
  it('累加进自己的流式条目，且排在正文之前', () => {
    const view = fold([started, reasoningOne, reasoningTwo, deltaOne])

    expect(shape(view.entries).map(([, kind, text]) => [kind, text])).toEqual([
      ['assistant', '先看目录。再读文件。'],
      ['assistant', '回'],
    ])
    expect(view.entries[0]!.streaming).toBe(true)
    expect(view.entries[0]!.id).not.toBe(view.entries[1]!.id)
  })

  it('正文落地后思维链条目保留但不再流式', () => {
    const view = fold([started, reasoningOne, deltaOne, assistantMessage])

    expect(view.entries.filter((entry) => entry.streaming === true)).toHaveLength(0)
    expect(view.entries.map((entry) => entry.text)).toEqual(['先看目录。', '回答'])
  })
})

describe('F4：工具调用生命周期', () => {
  const toolStarted = ev('tool_call_started', 2, {
    tool: 'read_file',
    tool_call_id: 'call-1',
    arguments: { path: 'a.txt' },
  })

  it('started → running，声明里带工具名与参数', () => {
    const view = fold([started, toolStarted])

    expect(view.tools).toHaveLength(1)
    expect(view.tools[0]).toMatchObject({
      toolCallId: 'call-1',
      tool: 'read_file',
      args: { path: 'a.txt' },
      status: 'running',
      resultText: '',
      durationMs: null,
    })
    expect(shape(view.entries)).toEqual([['tool:call-1', 'tool', '']])
    expect(view.entries[0]).toMatchObject({ tool: 'read_file', status: 'running' })
  })

  it('finished 落 duration_ms 与 content，并按 tool_call_id 合并', () => {
    const view = fold([
      started,
      toolStarted,
      ev('tool_call_finished', 3, {
        tool: 'read_file',
        tool_call_id: 'call-1',
        status: 'ok',
        content: '文件内容',
        duration_ms: 12,
      }),
    ])

    expect(view.tools).toHaveLength(1)
    expect(view.tools[0]).toMatchObject({ status: 'ok', resultText: '文件内容', durationMs: 12 })
    expect(view.entries[0]).toMatchObject({ text: '文件内容', status: 'ok', durationMs: 12 })
  })

  it('服务端的 failed / truncated 归一成本视图的三档', () => {
    const failedView = fold([
      started,
      toolStarted,
      ev('tool_call_finished', 3, { tool: 'read_file', tool_call_id: 'call-1', status: 'failed' }),
    ])
    expect(failedView.tools[0]!.status).toBe('error')

    const truncatedView = fold([
      started,
      toolStarted,
      ev('tool_call_finished', 3, { tool: 'read_file', tool_call_id: 'call-1', status: 'truncated' }),
    ])
    expect(truncatedView.tools[0]!.status).toBe('ok')
  })

  it('denied 落 denied 状态，且 durable 的 denied 正文不覆盖拒绝理由', () => {
    const view = fold([
      started,
      ev('tool_call_denied', 2, {
        tool: 'shell',
        tool_call_id: 'call-2',
        reason: '命令越界',
        status: 'denied',
      }),
      ev('tool_result_message', 3, {
        entry_id: 'entry-denied',
        message: { role: 'tool', content: 'Permission denied.', tool_call_id: 'call-2' },
      }),
    ])

    expect(view.tools[0]).toMatchObject({ status: 'denied', resultText: '命令越界' })
  })

  it('子 agent 标记落到 ToolRun.subagent', () => {
    const view = fold([
      started,
      ev('tool_call_started', 2, {
        tool: 'subagent',
        tool_call_id: 'call-3',
        subagent: { task: '查资料', index: 2 },
      }),
    ])

    expect(view.tools[0]!.subagent).toEqual({ task: '查资料', index: 2 })
  })
})

describe('F5：审批队列', () => {
  const requested = ev('approval_requested', 2, {
    approval_id: 'ap-1',
    tool: 'shell',
    arguments: { cmd: 'rm -rf /' },
    reason: '危险命令',
    created_at: 1_002,
    expires_at: 9_999,
  })

  it('requested → awaiting_approval，重放不叠第二条', () => {
    const once = fold([started, requested])
    expect(once.phase).toBe('awaiting_approval')
    expect(once.approvals).toHaveLength(1)
    expect(once.approvals[0]).toMatchObject({
      approval_id: 'ap-1',
      tool: 'shell',
      reason: '危险命令',
      decision: null,
    })

    const twice = applyEvent(once, { ...requested, seq: 9 })
    expect(twice.approvals).toHaveLength(1)
  })

  it('resolved → 按 approval_id 收敛，phase 回到 running', () => {
    const view = fold([
      started,
      requested,
      ev('approval_resolved', 3, { approval_id: 'ap-1', decision: 'deny', reason: '不许' }),
    ])

    expect(view.approvals).toEqual([])
    expect(view.phase).toBe('running')

    const again = applyEvent(view, ev('approval_resolved', 9, { approval_id: 'ap-1', decision: 'deny' }))
    expect(again.approvals).toEqual([])
  })
})

describe('F6：终态错误', () => {
  it('run_failed 填 error.code / error.message', () => {
    const view = fold([started, failedEvent(2, { code: 'llm_error', message: '模型不可达' })])

    expect(view.phase).toBe('failed')
    expect(view.error).toEqual({ code: 'llm_error', message: '模型不可达' })
  })

  it('run_failed 缺 code 时回落 internal，不显示空错误', () => {
    const view = fold([started, ev('run_failed', 2, {})])
    expect(view.error?.code).toBe('internal')
  })

  it('run_started 清掉上一次的错误', () => {
    const view = fold([
      started,
      failedEvent(2, { code: 'x', message: 'y' }),
      ev('run_started', 3),
    ])
    expect(view.error).toBeNull()
  })
})

describe('F10 / F11 / F13 / F14：提醒、状态、resync 与前向兼容', () => {
  const compacted = ev('context_compacted', 2, {
    step: 'summarize',
    detail: '折叠了 12 条旧消息',
    before: 8_000,
    after: 3_000,
  })

  it('F10：context_compacted 同时进 compactions 与 notice 条目，重放不重复', () => {
    const view = fold([started, compacted])

    expect(view.compactions).toEqual([{ ts: 1_002, step: 'summarize', before: 8_000, after: 3_000 }])
    expect(view.entries).toHaveLength(1)
    expect(view.entries[0]).toMatchObject({ kind: 'notice', notice: 'compaction' })
    expect(view.entries[0]!.text).toContain('summarize')

    const again = applyEvent(view, { ...compacted, seq: 9 })
    expect(again.compactions).toHaveLength(1)
    expect(again.entries).toHaveLength(1)
  })

  it('F10：todo_reminder / stop_nudge 各推一条 notice，按 entry_id 去重', () => {
    const todo = fold([
      started,
      ev('todo_reminder', 2, { entry_id: 'entry-todo', content: '还有 2 项没做' }),
    ])
    expect(todo.entries[0]).toMatchObject({ kind: 'notice', notice: 'todo', text: '还有 2 项没做' })
    // 换了 seq 的同一条提醒仍然只留一条：去重键是 entry_id，不是 seq。
    expect(
      applyEvent(todo, ev('todo_reminder', 9, { entry_id: 'entry-todo', content: '还有 2 项没做' }))
        .entries,
    ).toHaveLength(1)

    const nudge = fold([started, ev('stop_nudge', 2, { entry_id: 'entry-nudge', content: '你没有回答' })])
    expect(nudge.entries[0]).toMatchObject({ kind: 'notice', notice: 'nudge', text: '你没有回答' })
    expect(
      applyEvent(nudge, ev('stop_nudge', 9, { entry_id: 'entry-nudge', content: '你没有回答' }))
        .entries,
    ).toHaveLength(1)
  })

  it('F11：run_status 更新 round / tokens / usage / activity，且不动 seq', () => {
    const view = fold([
      started,
      ev('run_status', null, { round: 2, tokens: 100, usage: USAGE, activity: 'model' }),
    ])

    expect(view).toMatchObject({ round: 2, tokens: 100, activity: 'model' })
    expect(view.usage).toEqual(USAGE)
    expect(view.seq).toBe(1)
  })

  it('F11：带 subagent 的状态不改父运行的轮次与用量', () => {
    const parent = fold([started, ev('run_status', null, { round: 2, tokens: 100, activity: 'model' })])
    const child = applyEvent(
      parent,
      ev('run_status', null, { round: 1, tokens: 7, activity: 'model', subagent: { task: '查资料', index: 1 } }),
    )

    expect(child).toMatchObject({ round: 2, tokens: 100 })
  })

  it('F13：resync 置 detached', () => {
    const view = fold([started, ev('resync', 2, { after_seq: 40, reason: 'buffer_evicted' })])
    expect(view.detached).toBe(true)
  })

  it('F14：未知事件类型不抛错，也不改动视图', () => {
    const view = fold([started, userMessage])
    const unknown = { ...ev('run_status', null), type: 'future_event' as AvidEventType }

    expect(() => applyEvent(view, unknown)).not.toThrow()
    // 通用字段之外没有可改的东西（seq 为 null 时连游标都不动）。
    expect(shape(applyEvent(view, unknown).entries)).toEqual(shape(view.entries))
  })
})

describe('F7：viewFromEntries 从落盘条目重建', () => {
  function entry(
    entry_id: string,
    seq: number,
    message: Record<string, unknown> | null,
    type = 'message',
  ): Entry {
    return { entry_id, parent_id: null, seq, timestamp: 1_000 + seq, type, message }
  }

  it('F7：与事件路径的条目逐字段同形', () => {
    const viaEvents = fold([started, userMessage, assistantMessage, finished])

    const viaEntries = viewFromEntries(emptyView(SESSION), [
      entry('entry-user', 2, { role: 'user', content: '问题' }),
      entry('entry-assistant', 3, { role: 'assistant', content: '回答' }),
    ])

    expect(viaEntries.entries).toEqual(viaEvents.entries)
    expect(viaEntries.seq).toBe(3)
    expect(viaEntries.detached).toBe(false)
  })

  it('F7：工具调用两条路径同形（时间戳与耗时除外）', () => {
    const calls = [
      {
        id: 'call-1',
        type: 'function',
        function: { name: 'read_file', arguments: '{"path":"a.txt"}' },
      },
    ]
    const viaEvents = fold([
      started,
      ev('assistant_message', 2, {
        entry_id: 'entry-assistant',
        message: { role: 'assistant', content: '我来读文件', tool_calls: calls },
      }),
      ev('tool_call_started', 3, {
        tool: 'read_file',
        tool_call_id: 'call-1',
        arguments: { path: 'a.txt' },
      }),
      ev('tool_call_finished', 4, {
        tool: 'read_file',
        tool_call_id: 'call-1',
        status: 'ok',
        duration_ms: 12,
      }),
      ev('tool_result_message', 5, {
        entry_id: 'entry-tool',
        message: { role: 'tool', content: '文件内容', tool_call_id: 'call-1' },
      }),
    ])

    const viaEntries = viewFromEntries(emptyView(SESSION), [
      entry('entry-assistant', 2, {
        role: 'assistant',
        content: '我来读文件',
        tool_calls: calls,
      }),
      entry('entry-tool', 5, { role: 'tool', content: '文件内容', tool_call_id: 'call-1' }),
    ])

    expect(entryShape(viaEntries.entries)).toEqual(entryShape(viaEvents.entries))
    expect(toolShape(viaEntries.tools)).toEqual(toolShape(viaEvents.tools))
    expect(toolShape(viaEntries.tools)[0]).toMatchObject({
      toolCallId: 'call-1',
      tool: 'read_file',
      args: { path: 'a.txt' },
      status: 'ok',
      resultText: '文件内容',
    })
  })

  it('F7：工具状态在离线侧按内容前缀推断（镜像 schemas.classify_tool_status）', () => {
    const rebuild = (content: string) =>
      viewFromEntries(emptyView(SESSION), [
        entry('entry-tool', 2, { role: 'tool', content, tool_call_id: 'call-9' }),
      ]).tools[0]!.status

    expect(rebuild('Permission denied.')).toBe('denied')
    expect(rebuild('错误：文件不存在')).toBe('error')
    expect(rebuild('参数错误：缺 path')).toBe('error')
    expect(rebuild('工具执行失败：read_file（boom）')).toBe('error')
    expect(rebuild('一切正常')).toBe('ok')
  })

  it('F7：内核注入的 notice 条目重建为 notice（档位在磁盘上没有记录，回落到 info）', () => {
    const view = viewFromEntries(emptyView(SESSION), [
      entry('entry-notice', 2, { role: 'user', content: '还有 2 项没做' }, 'notice'),
    ])

    expect(view.entries).toEqual([
      { id: 'entry-notice', kind: 'notice', ts: 1_002, text: '还有 2 项没做', notice: 'info' },
    ])
  })

  it('F7：乱序条目按 seq 排序，未知类型跳过', () => {
    const view = viewFromEntries(emptyView(SESSION), [
      entry('b', 5, { role: 'assistant', content: '后面的' }),
      entry('a', 2, { role: 'user', content: '前面的' }),
      entry('weird', 3, null, 'value'),
    ])

    expect(view.entries.map((item) => item.text)).toEqual(['前面的', '后面的'])
    expect(view.seq).toBe(5)
  })

  it('F7：空条目列表把视图清成空（resync 后没有历史也不留旧条目）', () => {
    const withHistory = fold([started, userMessage, assistantMessage])
    const cleared = viewFromEntries(withHistory, [])

    expect(cleared.entries).toEqual([])
    expect(cleared.tools).toEqual([])
    expect(cleared.detached).toBe(false)
  })
})
