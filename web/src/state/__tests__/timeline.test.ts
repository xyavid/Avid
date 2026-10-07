/**
 * 时间线纯函数用例。
 *
 * 核心是那条不变量：**同一次运行，事件流逐条建出的段落与「重新读会话」建出的
 * 段落逐项同形**（live-only 段除外）。它守住「过程与收尾同一套渲染」——
 * 两边一旦漂移，收尾就会跳变，而这正是本阶段要治的病。
 */

import { describe, expect, it } from 'vitest'

import type { Entry } from '../../api/types'
import type { TimelineEvent, TimelineItem } from '../timeline'
import { applyEvent, itemsFromEntries, mergeItems } from '../timeline'

function entry(seq: number, message: Record<string, unknown>, type = 'message'): Entry {
  return { entry_id: `e${seq}`, parent_id: null, seq, timestamp: seq, type, message }
}

function ev(type: string, ts: number, data: Record<string, unknown> = {}): TimelineEvent {
  return { type, ts, data }
}

type RawCall = { id: string; name: string; args: string }

/** 助手消息事件的载荷：与 svc 的 _message_sink 同形（entry_id + message）。 */
function assistantEvent(entryId: string, content: string, calls: RawCall[] = [], ts = 0) {
  return ev('assistant_message', ts, {
    entry_id: entryId,
    message: {
      role: 'assistant',
      content,
      ...(calls.length > 0
        ? {
            tool_calls: calls.map((c) => ({
              id: c.id,
              type: 'function',
              function: { name: c.name, arguments: c.args },
            })),
          }
        : {}),
    },
  })
}

function assistantEntry(seq: number, content: string, calls: RawCall[] = []): Entry {
  return entry(seq, {
    role: 'assistant',
    content,
    ...(calls.length > 0
      ? {
          tool_calls: calls.map((c) => ({
            id: c.id,
            type: 'function',
            function: { name: c.name, arguments: c.args },
          })),
        }
      : {}),
  })
}

/** 两条路径必须一致的那部分：live-only 段与只属于运行期的读数（耗时、流式标记）除外。 */
function durableShape(items: TimelineItem[]) {
  return items
    .filter((item) => item.kind !== 'reasoning')
    .map((item) =>
      item.kind === 'tool'
        ? {
            kind: item.kind,
            callId: item.callId,
            name: item.name,
            args: item.args,
            result: item.result,
            status: item.status,
          }
        : { kind: item.kind, entryId: item.entryId, text: item.text },
    )
}

function replay(events: TimelineEvent[], from: TimelineItem[] = []): TimelineItem[] {
  return events.reduce(applyEvent, from)
}

describe('itemsFromEntries：会话条目 → 段落', () => {
  it('正文排在它触发的工具之前（轮内顺序 = 模型先说话、再动手）', () => {
    const items = itemsFromEntries([
      entry(1, { role: 'user', content: '跑一下' }),
      assistantEntry(2, '我先跑一遍。', [{ id: 'c1', name: 'bash', args: '{"command":"ls"}' }]),
      entry(3, { role: 'tool', tool_call_id: 'c1', content: 'a.py' }),
    ])

    expect(items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tool'])
    expect(items[1]).toMatchObject({ kind: 'assistant', entryId: 'e2', text: '我先跑一遍。', streaming: false })
  })

  it('工具结果按 tool_call_id 归位；失败口径与后端 classify_tool_status 一致', () => {
    const items = itemsFromEntries([
      assistantEntry(2, '', [
        { id: 'c1', name: 'bash', args: '{"command":"boom"}' },
        { id: 'c2', name: 'bash', args: '{"command":"ok"}' },
        { id: 'c3', name: 'bash', args: '{"command":"never"}' },
      ]),
      entry(3, { role: 'tool', tool_call_id: 'c1', content: '错误：命令被拒绝' }),
      entry(4, { role: 'tool', tool_call_id: 'c2', content: '执行失败：exit 1' }),
    ])

    expect(items.map((i) => (i.kind === 'tool' ? i.status : i.kind))).toEqual(['failed', 'failed', 'running'])
    expect(items[2]).toMatchObject({ kind: 'tool', callId: 'c3', result: null })
  })

  it('notice 条目不进时间线（内核注入的提醒不是对话）', () => {
    const items = itemsFromEntries([
      entry(1, { role: 'user', content: '在吗' }),
      entry(2, { role: 'user', content: '[提醒] 连续三轮未更新清单' }, 'notice'),
    ])

    expect(items).toHaveLength(1)
  })

  it('subagent 调用只剩调用与参数，子步骤是 live-only（刷新后不在了）', () => {
    const args = JSON.stringify({ tasks: [{ description: '前端改造', prompt: '...' }] })
    const items = itemsFromEntries([assistantEntry(2, '', [{ id: 'c1', name: 'subagent', args }])])

    expect(items[0]).toMatchObject({ kind: 'tool', name: 'subagent', args, steps: [] })
  })
})

describe('applyEvent：事件 → 段落增量', () => {
  it('不变量：事件流建出的段落与重新读会话建出的段落同形', () => {
    const events: TimelineEvent[] = [
      ev('run_started', 1, { permission: 'normal' }),
      ev('user_message', 2, { entry_id: 'e1', message: { role: 'user', content: '跑一下测试' } }),
      ev('reasoning_delta', 3, { text: '先看仓库结构。' }),
      ev('reasoning_delta', 5, { text: '再跑测试。' }),
      ev('assistant_delta', 10, { text: '我先跑一遍' }),
      ev('assistant_delta', 12, { text: '。' }),
      assistantEvent('e2', '我先跑一遍。', [{ id: 'c1', name: 'bash', args: '{"command":"uv run pytest -q"}' }], 13),
      ev('tool_call_started', 14, { tool: 'bash', tool_call_id: 'c1', arguments: { command: 'uv run pytest -q' } }),
      ev('tool_result_message', 20, { entry_id: 'e3', message: { role: 'tool', tool_call_id: 'c1', content: '271 passed' } }),
      ev('tool_call_finished', 21, { tool: 'bash', tool_call_id: 'c1', status: 'ok', duration_ms: 8000 }),
      ev('reasoning_delta', 30, { text: '过了。' }),
      assistantEvent('e4', '271 项全过。', [], 40),
      ev('run_finished', 41, { text: '271 项全过。' }),
    ]
    const entries: Entry[] = [
      entry(1, { role: 'user', content: '跑一下测试' }),
      assistantEntry(2, '我先跑一遍。', [{ id: 'c1', name: 'bash', args: '{"command":"uv run pytest -q"}' }]),
      entry(3, { role: 'tool', tool_call_id: 'c1', content: '271 passed' }),
      assistantEntry(4, '271 项全过。'),
    ]

    expect(durableShape(replay(events))).toEqual(durableShape(itemsFromEntries(entries)))
  })

  it('思考按到达顺序成段，夹在中间的工具会切开两段', () => {
    const items = replay([
      ev('reasoning_delta', 1000, { text: '想 A' }),
      ev('reasoning_delta', 4200, { text: '，还想 B' }),
      assistantEvent('e2', '先查一下。', [{ id: 'c1', name: 'bash', args: '{}' }], 4300),
      ev('reasoning_delta', 9000, { text: '想 C' }),
    ])

    expect(items.map((i) => i.kind)).toEqual(['reasoning', 'assistant', 'tool', 'reasoning'])
    expect(items[0]).toMatchObject({ kind: 'reasoning', text: '想 A，还想 B', startedAt: 1000, endedAt: 4200, streaming: false })
    expect(items[3]).toMatchObject({ kind: 'reasoning', text: '想 C', startedAt: 9000, streaming: true })
  })

  it('流式正文原地提交：assistant_message 到达时把流式段收成持久段，不新增段落', () => {
    const items = replay([
      ev('user_message', 1, { entry_id: 'e1', message: { role: 'user', content: '嗨' } }),
      ev('assistant_delta', 2, { text: '你' }),
      ev('assistant_delta', 3, { text: '好' }),
      assistantEvent('e2', '你好', [], 4),
    ])

    expect(items).toHaveLength(2)
    expect(items[1]).toMatchObject({ kind: 'assistant', entryId: 'e2', text: '你好', streaming: false })
  })

  it('重放幂等：同一批事件再放一遍，段落不翻倍（中途刷新附着靠这条）', () => {
    const events = [
      ev('user_message', 1, { entry_id: 'e1', message: { role: 'user', content: '嗨' } }),
      assistantEvent('e2', '我去看一眼。', [{ id: 'c1', name: 'read_file', args: '{"path":"/w/a.py"}' }], 2),
      ev('tool_call_started', 3, { tool: 'read_file', tool_call_id: 'c1', arguments: { path: '/w/a.py' } }),
      ev('tool_result_message', 4, { entry_id: 'e3', message: { role: 'tool', tool_call_id: 'c1', content: 'print(1)' } }),
      ev('tool_call_finished', 5, { tool: 'read_file', tool_call_id: 'c1', status: 'ok', duration_ms: 12 }),
    ]

    const once = replay(events)
    const twice = replay(events, once)

    expect(durableShape(twice)).toEqual(durableShape(once))
  })

  it('重放不把已完成的工具拨回运行中（顺序颠倒也不降级）', () => {
    const items = replay([
      ev('tool_call_started', 1, { tool: 'bash', tool_call_id: 'c1', arguments: { command: 'ls' } }),
      ev('tool_call_finished', 2, { tool: 'bash', tool_call_id: 'c1', status: 'ok', duration_ms: 5 }),
      ev('tool_call_started', 3, { tool: 'bash', tool_call_id: 'c1', arguments: { command: 'ls' } }),
    ])

    expect(items[0]).toMatchObject({ kind: 'tool', status: 'ok' })
  })

  it('用户消息把乐观气泡就地收编（发送时先画，事件到了认领同一个）', () => {
    const optimistic: TimelineItem[] = [{ kind: 'user', entryId: null, text: '嗨' }]
    const items = replay([ev('user_message', 1, { entry_id: 'e1', message: { role: 'user', content: '嗨' } })], optimistic)

    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'user', entryId: 'e1', text: '嗨' })
  })

  it('子 agent 事件折进对应任务的步骤里，父时间线不出现它的工具行', () => {
    const args = JSON.stringify({ tasks: [{ description: '前端改造', prompt: '...' }] })
    const items = replay([
      assistantEvent('e2', '派活。', [{ id: 'call_sub', name: 'subagent', args }], 1),
      ev('tool_call_started', 2, { tool: 'subagent', tool_call_id: 'call_sub', arguments: { tasks: JSON.parse(args).tasks } }),
      ev('tool_call_started', 3, { tool: 'read_file', tool_call_id: 'child1', arguments: { path: '/w/x.tsx' }, subagent: { task: '前端改造', index: 0 } }),
      ev('tool_call_finished', 4, { tool: 'read_file', tool_call_id: 'child1', status: 'ok', duration_ms: 7, subagent: { task: '前端改造', index: 0 } }),
    ])

    expect(items.map((i) => i.kind)).toEqual(['assistant', 'tool'])
    const card = items[1]
    if (card?.kind !== 'tool') throw new Error('期望第二段是 subagent 工具卡')
    expect(card.steps).toEqual([
      { task: '前端改造', callId: 'child1', name: 'read_file', args: JSON.stringify({ path: '/w/x.tsx' }), status: 'ok' },
    ])
  })

  it('认不出归属的子 agent 事件退化成父时间线上的一行（不丢信息）', () => {
    const items = replay([
      ev('tool_call_started', 1, { tool: 'read_file', tool_call_id: 'orphan', arguments: {}, subagent: { task: '无主任务', index: 0 } }),
    ])

    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'tool', callId: 'orphan' })
  })

  it('工具事件先于助手消息到达时自建一行（重放里顺序可能倒过来）', () => {
    const items = replay([
      ev('tool_call_started', 1, { tool: 'glob', tool_call_id: 'c9', arguments: { pattern: '*.py' } }),
      ev('tool_call_denied', 2, { tool: 'glob', tool_call_id: 'c9', reason: '用户拒绝' }),
    ])

    // 拒绝在界面上并入失败：重读会话时「被拒」只能按结果文案认，两侧口径必须一致。
    expect(items[0]).toMatchObject({ kind: 'tool', name: 'glob', status: 'failed' })
  })
})

describe('mergeItems：历史与本次运行的归并', () => {
  it('无重叠时就是追加', () => {
    const history = itemsFromEntries([entry(1, { role: 'user', content: '旧' })])
    const run = itemsFromEntries([entry(2, { role: 'assistant', content: '新' })])

    expect(mergeItems(history, run)).toHaveLength(2)
  })

  it('切换会话回来：重复的持久段按 entry_id 去重，思考段按锚点插回原位', () => {
    const history = itemsFromEntries([
      entry(1, { role: 'user', content: '跑一下' }),
      assistantEntry(2, '先跑。', [{ id: 'c1', name: 'bash', args: '{"command":"ls"}' }]),
      entry(3, { role: 'tool', tool_call_id: 'c1', content: 'ok' }),
      assistantEntry(4, '完成'),
    ])
    const run = replay([
      ev('reasoning_delta', 10, { text: '看看再说' }),
      ev('tool_call_started', 11, { tool: 'bash', tool_call_id: 'c1', arguments: { command: 'ls' } }),
    ])

    const merged = mergeItems(history, run)

    expect(merged.map((i) => i.kind)).toEqual(['user', 'assistant', 'reasoning', 'tool', 'assistant'])
    expect(merged[3]).toMatchObject({ kind: 'tool', callId: 'c1', result: 'ok', status: 'ok' })
  })

  it('运行里的段比历史新时追加在尾部（思考段跟在最后一个持久段之后）', () => {
    const history = itemsFromEntries([entry(1, { role: 'user', content: '跑一下' })])
    const run = replay([
      ev('reasoning_delta', 5, { text: '想想' }),
      assistantEvent('e2', '好', [], 6),
    ])

    expect(mergeItems(history, run).map((i) => i.kind)).toEqual(['user', 'reasoning', 'assistant'])
  })
})
