/**
 * 时间线：一次会话里的有序段落（历史 + 每次运行），过程与收尾共用同一套。
 *
 * 两个来源归并到同一个列表：
 * - **durable 条目**——会话落库后的回拉（`itemsFromEntries`）与运行中的消息事件
 *   （`user_message` / `assistant_message` / `tool_result_message`）同形，都带 `entry_id`；
 * - **live-only 段**——思考（`reasoning_delta`）与子 agent 的子步骤：不落盘、不重放，
 *   只在该次连接里存在，刷新或切会话后消失（见 `ReasoningBlock` 的注释）。
 *
 * 不变量：同一次运行，事件流逐条建出的段落与「重新读会话」建出的段落**逐项同形**
 * （live-only 段除外）。它就是「收尾不跳变」这句话的可执行形式，
 * `__tests__/timeline.test.ts` 钉住它。
 *
 * 归并幂等：按 `entry_id` / `tool_call_id` 去重。中途刷新会附着到运行并从 seq 0
 * 重放事件，重复投递不能变成重复段落。
 */

import type { Entry } from '../api/types'

/** 工具行状态。内核还有一档 `truncated`（内容被截断，不是调用失败）与 `denied`，
 *  两条路径都要给同一个答案：截断归 ok，拒绝归 failed——重读会话时这两个信息
 *  都不在条目里，只能按结果文本认（见 `classifyToolResult`）。 */
export type ToolStatus = 'running' | 'ok' | 'failed'

/** 子 agent 内部的一步：live-only（子运行不落库），刷新后随思考一起消失。 */
export type SubagentStep = {
  task: string
  callId: string
  name: string
  args: string
  status: ToolStatus
}

export type TimelineItem =
  | { kind: 'user'; entryId: string | null; text: string }
  | { kind: 'assistant'; entryId: string | null; text: string; streaming: boolean }
  | { kind: 'reasoning'; text: string; startedAt: number; endedAt: number; streaming: boolean }
  | {
      kind: 'tool'
      callId: string
      name: string
      args: string
      result: string | null
      status: ToolStatus
      durationMs: number | null
      steps: SubagentStep[]
    }

/** 内核事件的最小形状；`ts` 用来算思考段的持续时长。 */
export type TimelineEvent = { type: string; ts: number; data?: Record<string, unknown> }

type MessagePayload = {
  role?: unknown
  content?: unknown
  tool_calls?: Array<{ id?: unknown; function?: { name?: unknown; arguments?: unknown } }>
  tool_call_id?: unknown
}

/** 与 `avid/web/schemas.py` 的 classify_tool_status 同口径（前缀 + 长度窗口里的标记），
 *  另加内核拒绝时的固定文案——它不以「错误：」开头，重读会话只能按字面认。
 *  口径改动要两侧同步。 */
const FAILED_PREFIXES = ['错误：', '参数错误：', 'Permission denied.']
const FAILED_MARK = '执行失败：'

export function classifyToolResult(content: string): ToolStatus {
  const failed =
    FAILED_PREFIXES.some((prefix) => content.startsWith(prefix)) ||
    content.slice(0, 64).includes(FAILED_MARK)
  return failed ? 'failed' : 'ok'
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function messageContent(value: unknown): string {
  if (typeof value === 'string') return value
  return value === undefined || value === null ? '' : JSON.stringify(value)
}

/** 去重身份：有身份的段落（落库消息、工具调用）在两条路径里指向同一件事。 */
function identity(item: TimelineItem): string | null {
  if (item.kind === 'tool') return `tool:${item.callId}`
  if (item.kind === 'user' || item.kind === 'assistant') {
    return item.entryId === null ? null : `entry:${item.entryId}`
  }
  return null
}

/** React key：有身份的用身份，live-only 段用序号兜底（列表只追加，序号稳定）。 */
export function itemKey(item: TimelineItem, index: number): string {
  return identity(item) ?? `live:${item.kind}:${index}`
}

/** 发送时的乐观用户段：`user_message` 事件到达后就地收编，不再多出一条。 */
export function appendUser(items: TimelineItem[], text: string): TimelineItem[] {
  return [...items, { kind: 'user', entryId: null, text }]
}

/** 贴底跟随的签名：段落数 + 内容量。它变化 = 有新东西落进列表。 */
export function timelineSignature(items: TimelineItem[]): string {
  let chars = 0
  for (const item of items) {
    if (item.kind === 'tool') chars += item.args.length + (item.result?.length ?? 0) + item.steps.length * 32
    else chars += item.text.length
  }
  return `${items.length}:${chars}`
}

function indexOfTool(items: TimelineItem[], callId: string): number {
  return items.findIndex((item) => item.kind === 'tool' && item.callId === callId)
}

function lastIndexOf(items: TimelineItem[], match: (item: TimelineItem) => boolean): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (match(items[index]!)) return index
  }
  return -1
}

function replace(items: TimelineItem[], index: number, item: TimelineItem): TimelineItem[] {
  return [...items.slice(0, index), item, ...items.slice(index + 1)]
}

function emptyTool(callId: string, name: string, args: string): TimelineItem {
  return { kind: 'tool', callId, name, args, result: null, status: 'running', durationMs: null, steps: [] }
}

/** 思考定稿：正文/工具一到，正在流的思考段就闭段，之后再来的思考 delta 另起一段。
 *  只闭思考——正文的流式段由 assistant_message 原地提交，先闭就找不到了。 */
function closeReasoning(items: TimelineItem[]): TimelineItem[] {
  if (!items.some((item) => item.kind === 'reasoning' && item.streaming)) return items
  return items.map((item) =>
    item.kind === 'reasoning' && item.streaming ? { ...item, streaming: false } : item,
  )
}

// ---------------------------------------------------------------- 会话条目 → 段落

/** 会话条目 → 段落。轮内顺序 = 模型先说话、再动手：正文在前，它触发的工具在后。 */
export function itemsFromEntries(entries: Entry[]): TimelineItem[] {
  const items: TimelineItem[] = []
  const byCallId = new Map<string, number>()
  for (const entry of entries) {
    if (entry.type !== 'message' || entry.message === null) continue
    const message = entry.message as MessagePayload
    const role = str(message.role)
    if (role === 'user') {
      const text = messageContent(message.content)
      if (text) items.push({ kind: 'user', entryId: entry.entry_id, text })
      continue
    }
    if (role === 'assistant') {
      const text = messageContent(message.content)
      if (text.trim()) items.push({ kind: 'assistant', entryId: entry.entry_id, text, streaming: false })
      for (const call of message.tool_calls ?? []) {
        const callId = str(call.id)
        const name = str(call.function?.name)
        if (!callId || !name) continue
        byCallId.set(callId, items.length)
        items.push(emptyTool(callId, name, str(call.function?.arguments)))
      }
      continue
    }
    if (role === 'tool') {
      const callId = str(message.tool_call_id)
      const at = byCallId.get(callId)
      if (at === undefined) continue
      const content = messageContent(message.content)
      const item = items[at]
      if (item?.kind !== 'tool') continue
      items[at] = { ...item, result: content, status: classifyToolResult(content) }
    }
  }
  return items
}

// ---------------------------------------------------------------- 事件 → 段落增量

function subagentTag(data: Record<string, unknown>): { task: string; index: number } | null {
  const raw = data.subagent
  if (raw === null || typeof raw !== 'object') return null
  const tag = raw as Record<string, unknown>
  if (typeof tag.task !== 'string' || typeof tag.index !== 'number') return null
  return { task: tag.task, index: tag.index }
}

/** 子 agent 的子步骤归入它所属的 subagent 卡：父时间线上只留那一张卡。
 *  找不到归属时返回 null，调用方退化成父级的一行——宁可多一行，不能丢信息。 */
function withChildStep(
  items: TimelineItem[],
  event: TimelineEvent,
  tag: { task: string; index: number },
): TimelineItem[] | null {
  const data = event.data ?? {}
  const callId = str(data.tool_call_id)
  if (!callId) return null
  // 正在运行的 subagent 卡至多一张（该工具是独占调用），所以兜底可以取"最近一张"。
  const owned = lastIndexOf(
    items,
    (item) =>
      item.kind === 'tool' &&
      item.name === 'subagent' &&
      item.steps.some((step) => step.callId === callId || step.task === tag.task),
  )
  const at = owned >= 0 ? owned : lastIndexOf(items, (item) => item.kind === 'tool' && item.name === 'subagent' && item.status === 'running')
  if (at < 0) return null
  const card = items[at]
  if (card?.kind !== 'tool') return null

  if (event.type === 'tool_call_started') {
    if (card.steps.some((step) => step.callId === callId)) return items
    const step: SubagentStep = {
      task: tag.task,
      callId,
      name: str(data.tool),
      args: str(data.arguments) || (data.arguments === undefined ? '' : JSON.stringify(data.arguments)),
      status: 'running',
    }
    return replace(items, at, { ...card, steps: [...card.steps, step] })
  }
  if (!card.steps.some((step) => step.callId === callId)) return items
  const status: ToolStatus =
    event.type === 'tool_call_denied'
      ? 'failed'
      : str(data.status) === 'failed' || str(data.status) === 'denied'
        ? 'failed'
        : 'ok'
  const steps = card.steps.map((step) => (step.callId === callId ? { ...step, status } : step))
  return replace(items, at, { ...card, steps })
}

/** 一条事件喂进列表；unknown 事件与 live-only 之外的状态事件原样返回。
 *  纯函数且幂等：同一事件重复投递不改变列表。 */
export function applyEvent(items: TimelineItem[], event: TimelineEvent): TimelineItem[] {
  const data = event.data ?? {}
  const tag = subagentTag(data)
  // 子运行的 delta 不进父时间线：子 agent 不流式，真到了也只能是错位文本。
  if (tag && (event.type === 'assistant_delta' || event.type === 'reasoning_delta')) return items
  if (tag && event.type.startsWith('tool_call_')) {
    return withChildStep(items, event, tag) ?? applyToolEvent(items, event)
  }
  switch (event.type) {
    case 'user_message':
      return applyUserMessage(items, data)
    case 'assistant_message':
      return applyAssistantMessage(items, data)
    case 'assistant_delta':
      return applyAssistantDelta(items, data)
    case 'reasoning_delta':
      return applyReasoningDelta(items, event, data)
    case 'tool_call_started':
    case 'tool_call_finished':
    case 'tool_call_denied':
    case 'tool_result_message':
      return applyToolEvent(items, event)
    default:
      return items
  }
}

function applyUserMessage(items: TimelineItem[], data: Record<string, unknown>): TimelineItem[] {
  const entryId = str(data.entry_id)
  const message = data.message as MessagePayload | undefined
  const text = messageContent(message?.content)
  if (!entryId || !text) return items
  if (items.some((item) => item.kind === 'user' && item.entryId === entryId)) return items
  // 乐观气泡（发送时先画的那个）就地收编，不再多出一条。
  const last = items.at(-1)
  if (last?.kind === 'user' && last.entryId === null && last.text === text) {
    return replace(items, items.length - 1, { ...last, entryId })
  }
  return [...items, { kind: 'user', entryId, text }]
}

function applyAssistantMessage(items: TimelineItem[], data: Record<string, unknown>): TimelineItem[] {
  const entryId = str(data.entry_id)
  const message = data.message as MessagePayload | undefined
  if (!entryId) return items
  if (items.some((item) => item.kind === 'assistant' && item.entryId === entryId)) return items
  const text = messageContent(message?.content)
  let out = items
  const streaming = lastIndexOf(out, (item) => item.kind === 'assistant' && item.streaming)
  if (streaming >= 0) {
    // 原地提交：流式段就是这条消息（重放时 delta 已丢失，这一段的正文以持久消息为准）。
    const item = out[streaming]
    if (item?.kind === 'assistant') {
      out = replace(out, streaming, { ...item, entryId, text: text || item.text, streaming: false })
    }
  } else if (text.trim()) {
    out = [...out, { kind: 'assistant', entryId, text, streaming: false }]
  }
  out = closeReasoning(out)
  for (const call of message?.tool_calls ?? []) {
    const callId = str(call.id)
    const name = str(call.function?.name)
    if (!callId || !name) continue
    if (indexOfTool(out, callId) >= 0) continue
    out = [...out, emptyTool(callId, name, str(call.function?.arguments))]
  }
  return out
}

function applyAssistantDelta(items: TimelineItem[], data: Record<string, unknown>): TimelineItem[] {
  const text = str(data.text)
  if (!text) return items
  const out = closeReasoning(items)
  const at = lastIndexOf(out, (item) => item.kind === 'assistant' && item.streaming)
  const item = at >= 0 ? out[at] : undefined
  if (item?.kind === 'assistant') return replace(out, at, { ...item, text: item.text + text })
  return [...out, { kind: 'assistant', entryId: null, text, streaming: true }]
}

function applyReasoningDelta(
  items: TimelineItem[],
  event: TimelineEvent,
  data: Record<string, unknown>,
): TimelineItem[] {
  const text = str(data.text)
  if (!text) return items
  // 只接着**紧邻尾部**的思考段生长：中间来过工具或正文，就是新的一段思考。
  const at = items.length - 1
  const item = items[at]
  if (item?.kind === 'reasoning' && item.streaming) {
    return replace(items, at, { ...item, text: item.text + text, endedAt: event.ts })
  }
  return [...items, { kind: 'reasoning', text, startedAt: event.ts, endedAt: event.ts, streaming: true }]
}

function applyToolEvent(raw: TimelineItem[], event: TimelineEvent): TimelineItem[] {
  const items = closeReasoning(raw)
  const data = event.data ?? {}
  if (event.type === 'tool_result_message') {
    const message = data.message as MessagePayload | undefined
    const callId = str(message?.tool_call_id)
    const at = indexOfTool(items, callId)
    if (at < 0) return items
    const content = messageContent(message?.content)
    const item = items[at]
    if (item?.kind !== 'tool') return items
    return replace(items, at, {
      ...item,
      result: content,
      status: item.status === 'running' ? classifyToolResult(content) : item.status,
    })
  }

  const callId = str(data.tool_call_id)
  if (!callId) return items
  const at = indexOfTool(items, callId)
  if (event.type === 'tool_call_started') {
    if (at < 0) {
      const args = data.arguments === undefined ? '' : JSON.stringify(data.arguments)
      return [...items, emptyTool(callId, str(data.tool), args)]
    }
    const item = items[at]
    if (item?.kind !== 'tool' || item.status !== 'running') return items
    // 参数以助手消息里的原文为准，这里只在它缺失时补（重新序列化会改动键序）。
    return replace(items, at, { ...item, name: item.name || str(data.tool), args: item.args || JSON.stringify(data.arguments ?? {}) })
  }

  if (at < 0) return items
  const item = items[at]
  if (item?.kind !== 'tool') return items
  const status: ToolStatus =
    event.type === 'tool_call_denied' ? 'failed' : str(data.status) === 'failed' || str(data.status) === 'denied' ? 'failed' : 'ok'
  const durationMs = typeof data.duration_ms === 'number' ? data.duration_ms : item.durationMs
  const result = typeof data.content === 'string' ? data.content : item.result
  return replace(items, at, { ...item, status, durationMs, result })
}

// ---------------------------------------------------------------- 历史与运行归并

/** 把本次运行建出的段落并回会话历史：重复的持久段就地更新（运行期的读数更全），
 *  live-only 段按锚点插回原位——切走会话再切回来时，思考不会跑到列表尾巴上。 */
export function mergeItems(history: TimelineItem[], run: TimelineItem[]): TimelineItem[] {
  const at = new Map<string, number>()
  const out = history.map((item, index) => {
    const key = identity(item)
    if (key !== null) at.set(key, index)
    return item
  })
  const before = new Map<number, TimelineItem[]>()
  const after = new Map<number, TimelineItem[]>()
  const tail: TimelineItem[] = []

  run.forEach((item, index) => {
    const key = identity(item)
    const known = key === null ? undefined : at.get(key)
    if (known !== undefined) {
      out[known] = overlay(out[known]!, item)
      return
    }
    // 锚点：它前面的第一个持久段（历史里找得到）→ 插在它后面；
    // 否则它后面的第一个持久段 → 插在它前面；都没有 → 追加到尾部。
    let anchor = -1
    for (let back = index - 1; back >= 0; back -= 1) {
      const backKey = identity(run[back]!)
      if (backKey !== null && at.has(backKey)) {
        anchor = at.get(backKey)!
        break
      }
    }
    if (anchor >= 0) {
      after.set(anchor, [...(after.get(anchor) ?? []), item])
      return
    }
    let ahead = -1
    for (let next = index + 1; next < run.length; next += 1) {
      const nextKey = identity(run[next]!)
      if (nextKey !== null && at.has(nextKey)) {
        ahead = at.get(nextKey)!
        break
      }
    }
    if (ahead >= 0) {
      before.set(ahead, [...(before.get(ahead) ?? []), item])
      return
    }
    tail.push(item)
  })

  const merged: TimelineItem[] = []
  out.forEach((item, index) => {
    merged.push(...(before.get(index) ?? []), item, ...(after.get(index) ?? []))
  })
  return [...merged, ...tail]
}

/** 同一条持久段的两份：位置与内容以历史为准，运行期的读数（状态、耗时、子步骤）叠加。 */
function overlay(base: TimelineItem, run: TimelineItem): TimelineItem {
  if (base.kind !== 'tool' || run.kind !== 'tool') return base
  const rank: Record<ToolStatus, number> = { running: 1, ok: 2, failed: 2 }
  return {
    ...base,
    status: rank[run.status] > rank[base.status] ? run.status : base.status,
    result: run.result ?? base.result,
    durationMs: run.durationMs ?? base.durationMs,
    steps: run.steps.length > 0 ? run.steps : base.steps,
  }
}
