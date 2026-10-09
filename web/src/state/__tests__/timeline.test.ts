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
import { appendUser, applyEvent, itemsFromEntries, mergeItems, subagentRuns, subagentSteps, turnGroups } from '../timeline'

function entry(seq: number, message: Record<string, unknown>, type = 'message'): Entry {
  return { entry_id: `e${seq}`, parent_id: null, seq, timestamp: seq, type, message }
}

function userItem(entryId: string | null, text: string, ts: number | null = null): TimelineItem {
  return { kind: 'user', entryId, text, ts }
}

function answerItem(entryId: string | null, text: string, ts: number | null = null, streaming = false): TimelineItem {
  return { kind: 'assistant', entryId, text, streaming, ts }
}

/** 段落身份（与 timeline.ts 的 identity 同口径）：只给用例读。 */
function identityOf(item: TimelineItem): string {
  return item.kind === 'tool' ? `tool:${item.callId}` : item.kind === 'reasoning' ? 'live' : `entry:${item.entryId}`
}

function toolItem(callId: string): TimelineItem {
  return { kind: 'tool', callId, name: 'bash', args: '{}', result: null, status: 'ok', durationMs: null, runs: [] }
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

/** 两条路径必须一致的那部分：live-only 段、只属于运行期的读数（工具耗时、流式标记），
 *  以及时间读数本身——live 吃事件 ts、重读吃条目 timestamp，同一个写入的两次取时钟，
 *  相差就是写盘那几毫秒，显示精度是秒，折叠行的用时不会因此换一个档。 */
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

  it('error 条目（运行失败的记账）进时间线；notice 不进', () => {
    const items = itemsFromEntries([
      entry(1, { role: 'user', content: '跑一下' }),
      entry(2, { role: 'assistant', content: '运行失败：请求超时' }, 'error'),
      entry(3, { role: 'user', content: '问题' }),
    ])

    expect(items.map((i) => i.kind)).toEqual(['user', 'error', 'user'])
    expect(items[1]).toMatchObject({ kind: 'error', entryId: 'e2', text: '运行失败：请求超时' })
    // 身份按条目 id：切会话回来时与重读会话的那条对齐，不重复
    expect(identityOf(items[1]!)).toBe('entry:e2')
  })

  it('run_failed 事件也落一段（live 路径与重读会话同形）', () => {
    const live = replay([
      ev('user_message', 1, { entry_id: 'e1', message: { role: 'user', content: '跑一下' } }),
      ev('run_failed', 2, {
        code: 'llm_error',
        message: '请求超时',
        text: '运行失败：请求超时',
        entry_id: 'e9',
      }),
    ])

    expect(live.map((i) => i.kind)).toEqual(['user', 'error'])
    // 展示句用内核给的 text（与落盘那条一字不差），不是原始 message
    expect(live[1]).toMatchObject({ kind: 'error', entryId: 'e9', text: '运行失败：请求超时' })

    // 同一事件重放一遍不翻倍（中途刷新附着靠这条）
    expect(
      replay(
        [ev('run_failed', 2, { code: 'llm_error', message: '请求超时', text: '运行失败：请求超时', entry_id: 'e9' })],
        live,
      ),
    ).toHaveLength(2)
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

    expect(items[0]).toMatchObject({ kind: 'tool', name: 'subagent', args, runs: [{ task: '前端改造', index: 0, items: [] }] })
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
    const optimistic: TimelineItem[] = [{ kind: 'user', entryId: null, text: '嗨', ts: 900 }]
    const items = replay([ev('user_message', 1, { entry_id: 'e1', message: { role: 'user', content: '嗨' } })], optimistic)

    expect(items).toHaveLength(1)
    // 读数换成服务端那份：本地时钟只是发送到事件到达之间的临时值
    expect(items[0]).toMatchObject({ kind: 'user', entryId: 'e1', text: '嗨', ts: 1 })
  })

  it('子运行的事件进它自己的段落列表（正文 / 思考 / 工具），父时间线不出现它的工具行', () => {
    const args = JSON.stringify({ tasks: [{ description: '前端改造', prompt: '...' }] })
    const items = replay([
      assistantEvent('e2', '派活。', [{ id: 'call_sub', name: 'subagent', args }], 1),
      ev('tool_call_started', 2, { tool: 'subagent', tool_call_id: 'call_sub', arguments: { tasks: JSON.parse(args).tasks } }),
      ev('reasoning_delta', 3, { text: '先看目录', subagent: { task: '前端改造', index: 0 } }),
      ev('assistant_delta', 4, { text: '我看一眼。', subagent: { task: '前端改造', index: 0 } }),
      ev('tool_call_started', 5, { tool: 'read_file', tool_call_id: 'child1', arguments: { path: '/w/x.tsx' }, subagent: { task: '前端改造', index: 0 } }),
      ev('tool_call_finished', 6, { tool: 'read_file', tool_call_id: 'child1', status: 'ok', duration_ms: 7, content: 'export const a = 1', subagent: { task: '前端改造', index: 0 } }),
    ])

    expect(items.map((i) => i.kind)).toEqual(['assistant', 'tool'])
    const card = items[1]
    if (card?.kind !== 'tool') throw new Error('期望第二段是 subagent 工具卡')
    expect(card.runs).toHaveLength(1)
    const [run] = card.runs
    expect(run).toMatchObject({ task: '前端改造', index: 0 })
    // 子运行的段落列表：思考 → 正文 → 工具（带结果）——与父时间线同一套模型
    expect(
      run!.items.map((i) =>
        i.kind === 'tool' ? `${i.name}:${i.result}` : i.kind === 'reasoning' ? `思考:${i.text}` : i.text,
      ),
    ).toEqual(['思考:先看目录', '我看一眼。', 'read_file:export const a = 1'])
    // 卡片折叠行的子步骤由 runs 派生（卡片 API 不变）
    expect(subagentSteps(card)).toEqual([
      { task: '前端改造', callId: 'child1', name: 'read_file', args: JSON.stringify({ path: '/w/x.tsx' }), status: 'ok' },
    ])
  })

  it('批已结束、迟到的增量仍归它那条子运行（合帧缓冲让它晚一步到）', () => {
    const args = JSON.stringify({ tasks: [{ description: '甲', prompt: 'a' }] })
    const items = replay([
      assistantEvent('e2', '派活。', [{ id: 'call_sub', name: 'subagent', args }], 1),
      ev('tool_call_started', 2, { tool: 'subagent', tool_call_id: 'call_sub', arguments: { tasks: JSON.parse(args).tasks } }),
      // 父级的收尾先到（子运行全回来了），子运行的最后一截正文还在合帧缓冲里
      ev('tool_call_finished', 3, { tool: 'subagent', tool_call_id: 'call_sub', status: 'ok', duration_ms: 10 }),
      ev('assistant_delta', 4, { text: '结论：2 行。', subagent: { task: '甲', index: 0 } }),
    ])

    const card = items[1]
    if (card?.kind !== 'tool') throw new Error('期望第二段是 subagent 工具卡')
    expect(card.status).toBe('ok')
    expect(card.runs[0]!.items).toMatchObject([{ kind: 'assistant', text: '结论：2 行。' }])
    // 批量结束那一刻子运行的流式正文收笔：面板里的游标不再闪
    expect(card.runs[0]!.items[0]).toMatchObject({ streaming: false })
    // 父时间线上不多出这一段
    expect(items.filter((i) => i.kind === 'assistant')).toHaveLength(1)
  })

  it('任务清单由参数播种：重读会话（明细不在）也列得出派过哪些任务', () => {
    const args = JSON.stringify({ tasks: [{ description: '甲', prompt: 'a' }, { description: '乙', prompt: 'b' }] })
    const items = itemsFromEntries([
      assistantEntry(1, '', [{ id: 'call_sub', name: 'subagent', args }]),
      entry(2, { role: 'tool', tool_call_id: 'call_sub', content: '已并行运行 2 个 subagent：…' }),
    ])

    const runs = subagentRuns(items)

    expect(runs.map((r) => `${r.task}:${r.items.length}:${r.running}`)).toEqual(['甲:0:false', '乙:0:false'])
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

describe('turnGroups：一次回话的分组与折叠判定', () => {
  /** 段落的可读身份：持久段用 entry_id、工具用 call_id、思考段就写它的种类。 */
  const ids = (list: TimelineItem[]) =>
    list.map((i) => (i.kind === 'tool' ? i.callId : i.kind === 'reasoning' ? 'reasoning' : i.entryId))

  it('一轮 = 一条用户消息到下一条用户消息；中间那一切进过程，末段正文是收尾', () => {
    const groups = turnGroups([
      userItem('e1', '跑一下'),
      answerItem('e2', '先读一遍。'),
      toolItem('c1'),
      answerItem('e3', '结论是 avid。'),
      userItem('e4', '第二问'),
      answerItem('e5', '第二答'),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0]!.items).toHaveLength(4)
    expect(ids(groups[0]!.process)).toEqual(['e2', 'c1'])
    expect(groups[0]!.answer).toMatchObject({ entryId: 'e3', text: '结论是 avid。' })
    // 收尾本身就是过程之后的那一段：折叠行落在用户段与收尾之间
    expect(groups[1]!.process).toEqual([])
    expect(groups[1]!.answer).toMatchObject({ entryId: 'e5' })
  })

  it('收尾必须是末段：末尾是工具（中断、失败）→ 这一轮没有收尾，界面据它决定折不折', () => {
    const groups = turnGroups([userItem('e1', '跑一下'), answerItem('e2', '去看一眼。'), toolItem('c1')])

    expect(groups[0]!.answer).toBeNull()
  })

  it('还在流的末段不算收尾（没有 entry_id：分叉点与时长都要磁盘上真实存在的那条）', () => {
    const groups = turnGroups([userItem('e1', '跑一下'), answerItem(null, '正在写', 9, true)])

    expect(groups[0]!.answer).toBeNull()
  })

  it('窗口从半轮中间开始（用户段不在这一页）→ 没有用户段，收尾照认', () => {
    const groups = turnGroups([toolItem('c1'), answerItem('e2', '补上一轮的回答')])

    expect(groups).toHaveLength(1)
    expect(groups[0]!.user).toBeNull()
    expect(groups[0]!.process).toEqual([groups[0]!.items[0]])
    expect(groups[0]!.answer).toMatchObject({ entryId: 'e2' })
  })

  it('用时 = 收尾段的读数 − 用户段的读数；缺读数或时钟倒挂 → null（折叠行只说「已完成」）', () => {
    const [ok] = turnGroups([userItem('e1', '问', 1_000), toolItem('c1'), answerItem('e2', '答', 74_000)])
    const [missing] = turnGroups([userItem('e1', '问'), answerItem('e2', '答')])
    const [backwards] = turnGroups([userItem('e1', '问', 5_000), answerItem('e2', '答', 1_000)])

    expect(ok!.durationMs).toBe(73_000)
    expect(missing!.durationMs).toBeNull()
    expect(backwards!.durationMs).toBeNull()
  })

  it('折叠开关的身份取自用户段（合并、重放都不会换 key）；半轮的组退回首段身份', () => {
    const first = turnGroups([userItem('e1', '问', 1), answerItem('e2', '答', 2)])
    const again = turnGroups([userItem('e1', '问', 1), answerItem('e2', '答', 2), userItem('e3', '又问', 3)])
    const head = turnGroups([answerItem('e2', '答', 2)])

    expect(first[0]!.key).toBe('entry:e1')
    expect(again[0]!.key).toBe('entry:e1')
    expect(head[0]!.key).toBe('entry:e2')
  })
})

describe('图片消息（阶段 59）', () => {
  const imagePart = { type: 'image', mime: 'image/png', name: 'shot.png', bytes: 120, index: 1 }

  it('条目里的图片块收成 stored 引用，文本仍进 text', () => {
    const items = itemsFromEntries([
      entry(1, { role: 'user', content: [{ type: 'text', text: '看这个' }, imagePart] }),
    ])

    expect(items[0]).toMatchObject({ kind: 'user', entryId: 'e1', text: '看这个' })
    expect(items[0]!.kind === 'user' && items[0]!.images).toEqual([
      { source: 'stored', entryId: 'e1', index: 1, name: 'shot.png', bytes: 120, mime: 'image/png' },
    ])
  })

  it('只有图没有文字也成一段（不该被「文字为空」吞掉）', () => {
    const items = itemsFromEntries([entry(1, { role: 'user', content: [imagePart] })])

    expect(items).toHaveLength(1)
    expect(items[0]!.kind === 'user' && items[0]!.text).toBe('')
    expect(items[0]!.kind === 'user' && items[0]!.images).toHaveLength(1)
  })

  it('没有图的用户消息不带 images 字段（老形态零变化）', () => {
    const items = itemsFromEntries([entry(1, { role: 'user', content: '一句话' })])

    expect(items[0]!.kind === 'user' && items[0]!.images).toBeUndefined()
  })

  it('user_message 事件把图片收编成同形的 stored 引用（收编那条乐观段）', () => {
    const optimistic = appendUser([], '看这个', [{ source: 'local', url: 'blob:x', name: 'shot.png' }])
    const items = applyEvent(
      optimistic,
      ev('user_message', 7, { entry_id: 'e9', message: { role: 'user', content: [{ type: 'text', text: '看这个' }, imagePart] } }),
    )

    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'user', entryId: 'e9', ts: 7 })
    expect(items[0]!.kind === 'user' && items[0]!.images).toEqual([
      { source: 'stored', entryId: 'e9', index: 1, name: 'shot.png', bytes: 120, mime: 'image/png' },
    ])
  })

  it('本地草稿段先带 object URL，落库后换成条目引用', () => {
    const draft = appendUser([], '', [{ source: 'local', url: 'blob:x', name: 'a.png' }])

    expect(draft[0]!.kind === 'user' && draft[0]!.images).toEqual([
      { source: 'local', url: 'blob:x', name: 'a.png' },
    ])
  })
})
