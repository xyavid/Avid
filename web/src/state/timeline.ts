/**
 * 时间线：一次会话里的有序段落（历史 + 每次运行），过程与收尾共用同一套。
 *
 * 三个来源归并到同一个列表：
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
 *
 * 收尾的折叠（`turnGroups`）也建在这份列表上：一轮跑完就把过程收成一行，
 * 只留收尾正文——它是纯函数，两条来源因此折出同一个形状。
 */

import type { Entry } from '../api/types'
import { subagentTasks } from './toolArgs'

/** 工具行状态。内核还有一档 `truncated`（内容被截断，不是调用失败）与 `denied`，
 *  两条路径都要给同一个答案：截断归 ok，拒绝归 failed——重读会话时这两个信息
 *  都不在条目里，只能按结果文本认（见 `classifyToolResult`）。 */
export type ToolStatus = 'running' | 'ok' | 'failed'

/**
 * 一个子 agent 任务**自己的段落列表**（阶段 53）：正文 / 思考 / 工具，与父时间线
 * 完全同一套模型——面板里的子运行界面就是拿它画的（`components/chat/Timeline` 复用）。
 *
 * 两部分来源不同，这也是「刷新后还剩什么」的答案：
 *   · 任务清单（`task` / `index`）来自**参数**（落在会话 JSONL 里），刷新后仍在；
 *   · `items` 来自**事件流**（子运行不落库、增量不重放），刷新后为空。
 */
export type SubagentRun = {
  task: string
  index: number
  items: TimelineItem[]
}

/** 子 agent 内部的一步（卡片折叠行用的扁平形状）：由 `runs` 里的工具段派生。 */
export type SubagentStep = {
  task: string
  callId: string
  name: string
  args: string
  status: ToolStatus
}

/**
 * 消息段的时间读数（毫秒），用来算一轮的「用时」：`turnGroups` 取用户段与收尾段之差。
 *
 * live 路径吃事件的 `ts`、重读路径吃条目的 `timestamp`——同一个写入的两次取时钟，
 * 相差就是写盘那几毫秒；折叠行精度到秒，两端又同向偏移（差值把写盘时间抵掉了），
 * 所以刷新前后读数是同一个档。两条路径都没有读数时为 null（折叠行只说「已完成」）。
 */
export type MessageTs = number | null

/**
 * 用户消息里的一张图（阶段 59）。
 *
 * 本地草稿带 object URL（还没落库，服务端取不到）；落库条目带 (entryId, 下标)——
 * 读侧端点的地址由**视图**拼，这里不碰 URL 形状（`api/` 才是端点形状的所在地）。
 */
export type TimelineImage =
  | { source: 'local'; url: string; name: string | null }
  | {
      source: 'stored'
      entryId: string
      index: number
      name: string | null
      bytes: number | null
      mime: string
    }

/**
 * 一条**还没被采纳**的输入（阶段 60）挂在用户段落上：它已经收下了，但还没进模型上下文。
 * 落库那一刻（事件带 input_id）这个标记消失，段落变成普通的用户消息。
 */
export type TimelinePending = {
  inputId: string
  mode: string
  /** 从「插入」降级成排队的（run 在它被领取前就结束了）。 */
  missed: boolean
  /** 待办输入里的图片张数：字节要等落库后才取得到（读端点按条目定位）。 */
  images: number
}

export type TimelineItem =
  | {
      kind: 'user'
      entryId: string | null
      text: string
      images?: TimelineImage[]
      pending?: TimelinePending
      ts: MessageTs
    }
  /** 运行失败的记账（阶段 55）：内核把它落成 error 条目，人或刷新都看得见，模型看不见。 */
  | { kind: 'error'; entryId: string | null; text: string }
  | { kind: 'assistant'; entryId: string | null; text: string; streaming: boolean; ts: MessageTs }
  | { kind: 'reasoning'; text: string; startedAt: number; endedAt: number; streaming: boolean }
  | {
      kind: 'tool'
      callId: string
      name: string
      args: string
      result: string | null
      status: ToolStatus
      durationMs: number | null
      /** 子运行各自的段落；非 subagent 工具恒为空数组。 */
      runs: SubagentRun[]
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

/** 用户消息内容 → (正文, 图片)：纯文本原样；分块数组里文本段拼正文、图片段收成引用。
 *
 *  两条路径（事件流 / 重读会话）必须给出**同形**的段落，所以解析只此一处——
 *  图片块在两边的形状由服务端保证一致（读侧都是不带字节的 ref）。
 */
export function userContent(
  content: unknown,
  entryId: string | null,
): { text: string; images: TimelineImage[] } {
  if (!Array.isArray(content)) return { text: messageContent(content), images: [] }
  const texts: string[] = []
  const images: TimelineImage[] = []
  content.forEach((part, index) => {
    if (part === null || typeof part !== 'object') return
    const block = part as Record<string, unknown>
    if (block.type === 'image') {
      if (entryId === null) return // 没有条目就没有可读地址，等 user_message 收编
      images.push({
        source: 'stored',
        entryId,
        index,
        name: typeof block.name === 'string' ? block.name : null,
        bytes: typeof block.bytes === 'number' ? block.bytes : null,
        mime: typeof block.mime === 'string' ? block.mime : '',
      })
      return
    }
    if (typeof block.text === 'string' && block.text) texts.push(block.text)
  })
  return { text: texts.join('\n'), images }
}

/** 去重身份：有身份的段落（落库消息、工具调用）在两条路径里指向同一件事。 */
function identity(item: TimelineItem): string | null {
  if (item.kind === 'tool') return `tool:${item.callId}`
  if (item.kind === 'user' || item.kind === 'assistant' || item.kind === 'error') {
    return item.entryId === null ? null : `entry:${item.entryId}`
  }
  return null
}

/** React key：有身份的用身份，live-only 段用序号兜底（列表只追加，序号稳定）。 */
export function itemKey(item: TimelineItem, index: number): string {
  return identity(item) ?? `live:${item.kind}:${index}`
}

/** 发送时的乐观用户段：`user_message` 事件到达后就地收编，不再多出一条。
 *  读数先用本地时钟占位（服务端与本机是同一台），事件到达即换成服务端那份。
 *  带图时图还是本地草稿（object URL）——收编时 `applyUserMessage` 换成条目引用。 */
export function appendUser(
  items: TimelineItem[],
  text: string,
  images: TimelineImage[] = [],
): TimelineItem[] {
  return [
    ...items,
    { kind: 'user', entryId: null, text, ...(images.length ? { images } : {}), ts: Date.now() },
  ]
}

/** 收下一条待办输入（投递成功、还没落库）：同一 input_id 只画一段。 */
export function appendPending(
  items: TimelineItem[],
  input: { inputId: string; mode: string; missed: boolean; images: number; text: string },
): TimelineItem[] {
  if (items.some((item) => item.kind === 'user' && item.pending?.inputId === input.inputId)) {
    return items
  }
  return [
    ...items,
    {
      kind: 'user',
      entryId: null,
      text: input.text,
      pending: {
        inputId: input.inputId,
        mode: input.mode,
        missed: input.missed,
        images: input.images,
      },
      ts: Date.now(),
    },
  ]
}

/** 撤销一条待办输入（服务端确认后调用）：把那段拿走。 */
export function dropPending(items: TimelineItem[], inputId: string): TimelineItem[] {
  const has = items.some((item) => item.kind === 'user' && item.pending?.inputId === inputId)
  if (!has) return items
  return items.filter((item) => !(item.kind === 'user' && item.pending?.inputId === inputId))
}

/** 打开会话时把服务端的待办输入画回来（刷新后仍然看得见「排队中」）。 */
export function mergePendingInputs(
  items: TimelineItem[],
  inputs: {
    input_id: string
    mode: string
    text: string
    images: number
    missed: boolean
  }[],
): TimelineItem[] {
  let merged = items
  for (const input of inputs) {
    merged = appendPending(merged, {
      inputId: input.input_id,
      mode: input.mode,
      missed: input.missed,
      images: input.images,
      text: input.text,
    })
  }
  return merged
}

/** 贴底跟随的签名：段落数 + 内容量。它变化 = 有新东西落进列表。 */
export function timelineSignature(items: TimelineItem[]): string {
  let chars = 0
  for (const item of items) {
    if (item.kind === 'tool') {
      const children = item.runs.reduce((n, run) => n + run.items.length, 0)
      chars += item.args.length + (item.result?.length ?? 0) + children * 32
    } else chars += item.text.length
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

function replace<T>(items: T[], index: number, item: T): T[] {
  return [...items.slice(0, index), item, ...items.slice(index + 1)]
}

function emptyTool(callId: string, name: string, args: string): TimelineItem {
  return {
    kind: 'tool',
    callId,
    name,
    args,
    result: null,
    status: 'running',
    durationMs: null,
    runs: name === 'subagent' ? seedRuns(args) : [],
  }
}

/** 子运行条目先用**参数**里的任务清单播种：面板在刷新后仍列得出这些任务（明细为空）。 */
function seedRuns(args: string): SubagentRun[] {
  return subagentTasks(args).map((task, index) => ({ task, index, items: [] }))
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
    if (entry.type === 'error') {
      const text = messageContent((entry.message as MessagePayload | null)?.content)
      if (text) items.push({ kind: 'error', entryId: entry.entry_id, text })
      continue
    }
    if (entry.type !== 'message' || entry.message === null) continue
    const message = entry.message as MessagePayload
    const role = str(message.role)
    if (role === 'user') {
      const { text, images } = userContent(message.content, entry.entry_id)
      if (text || images.length > 0) {
        items.push({
          kind: 'user',
          entryId: entry.entry_id,
          text,
          ...(images.length ? { images } : {}),
          ts: entry.timestamp,
        })
      }
      continue
    }
    if (role === 'assistant') {
      const text = messageContent(message.content)
      if (text.trim()) {
        items.push({ kind: 'assistant', entryId: entry.entry_id, text, streaming: false, ts: entry.timestamp })
      }
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

export function subagentTag(data: Record<string, unknown>): { task: string; index: number } | null {
  const raw = data.subagent
  if (raw === null || typeof raw !== 'object') return null
  const tag = raw as Record<string, unknown>
  if (typeof tag.task !== 'string' || typeof tag.index !== 'number') return null
  return { task: tag.task, index: tag.index }
}

function isSubagentCard(item: TimelineItem): item is Extract<TimelineItem, { kind: 'tool' }> {
  return item.kind === 'tool' && item.name === 'subagent'
}

/**
 * 带标记的事件归哪张卡。三档，顺序是刻意的：
 *   ① 已在跑、且认领过这条任务的卡——同名任务被重派时，旧卡不会被新批抢走事件；
 *   ② 最近一张还在跑的 subagent 卡（子运行是独占调用，同一时刻至多一张在跑）；
 *   ③ 最近一张 subagent 卡——批已经结束，但它的增量还压在合帧缓冲里等着落地，
 *      那时卡的状态已经是 ok，事件仍要找得到自己的家（否则子运行的正文会漏）。
 */
function subagentCardIndex(items: TimelineItem[], tag: { task: string; index: number }): number {
  const owns = (item: TimelineItem) =>
    isSubagentCard(item) && item.runs.some((run) => run.task === tag.task || run.index === tag.index)
  const byIdentity = lastIndexOf(items, (item) => owns(item) && item.kind === 'tool' && item.status === 'running')
  if (byIdentity >= 0) return byIdentity
  const running = lastIndexOf(items, (item) => isSubagentCard(item) && item.status === 'running')
  if (running >= 0) return running
  return lastIndexOf(items, isSubagentCard)
}

/** 认领一张卡里的某条子运行：按任务名找，找不到就按 index 补一条（参数里没有的任务）。 */
function runIndex(runs: SubagentRun[], tag: { task: string; index: number }): number {
  const at = runs.findIndex((run) => run.task === tag.task)
  if (at >= 0) return at
  const byIndex = runs.findIndex((run) => run.index === tag.index)
  return byIndex
}

/** 这一批跑完了：子运行最后那段流式正文收笔——游标不能在面板里一直闪。 */
function closeRunStreams(item: TimelineItem): TimelineItem {
  if (item.kind !== 'tool' || item.status === 'running' || item.runs.length === 0) return item
  const runs = item.runs.map((run) => ({
    ...run,
    items: run.items.map((child) =>
      child.kind === 'assistant' && child.streaming ? { ...child, streaming: false } : child,
    ),
  }))
  return { ...item, runs }
}

/** 子运行自己的段落增量：与父时间线**同一套**归并函数，只是列表换成它自己的。 */
function childItems(items: TimelineItem[], event: TimelineEvent, data: Record<string, unknown>): TimelineItem[] {
  switch (event.type) {
    case 'assistant_delta':
      return applyAssistantDelta(items, data)
    case 'reasoning_delta':
      return applyReasoningDelta(items, event, data)
    case 'tool_call_started':
    case 'tool_call_finished':
    case 'tool_call_denied':
      return applyToolEvent(items, event)
    default:
      // 子运行的其余事件（run_status / run_started / stop_nudge…）不进它的正文：
      // 状态与读数在父级那张卡上，正文只留"它说了什么、动了什么"。
      return items
  }
}

/** 带 subagent 标记的事件归入它那一条子运行；找不到归属时返回 null，调用方退化成父级一行。 */
function withChildEvent(
  items: TimelineItem[],
  event: TimelineEvent,
  tag: { task: string; index: number },
): TimelineItem[] | null {
  const at = subagentCardIndex(items, tag)
  if (at < 0) return null
  const card = items[at]
  if (card?.kind !== 'tool') return null

  const found = runIndex(card.runs, tag)
  const current: SubagentRun =
    found >= 0 ? card.runs[found]! : { task: tag.task, index: tag.index, items: [] }
  const nextItems = childItems(current.items, event, event.data ?? {})
  if (nextItems === current.items && found >= 0) return items // 与这条子运行无关的事件：不动列表

  const runs =
    found >= 0
      ? replace(card.runs, found, { ...current, items: nextItems })
      : [...card.runs, { ...current, items: nextItems }]
  // 卡已收尾（迟到的那一截）：收笔——不会再有事件来关它了
  return replace(items, at, closeRunStreams({ ...card, runs }))
}

/** 一条事件喂进列表；unknown 事件与 live-only 之外的状态事件原样返回。
 *  纯函数且幂等：同一事件重复投递不改变列表。 */
export function applyEvent(items: TimelineItem[], event: TimelineEvent): TimelineItem[] {
  const data = event.data ?? {}
  const tag = subagentTag(data)
  if (tag) {
    // 子运行的一切都折进它自己的段落列表（panel 画的就是它）：正文、思考、工具。
    // 归属认不出时退回父级一行——宁可多一行，不能丢信息。
    const folded = withChildEvent(items, event, tag)
    if (folded !== null) return folded
    if (event.type.startsWith('tool_call_')) return applyToolEvent(items, event)
    return items
  }
  switch (event.type) {
    case 'run_failed':
      return applyRunFailed(items, data)
    case 'user_message':
      return applyUserMessage(items, event, data)
    case 'assistant_message':
      return applyAssistantMessage(items, event, data)
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

/** 运行失败：就地落一段错误段（带 entry_id 时按身份去重，重读会话不会多出一条）。
 *  它是这次运行唯一的产出，界面必须留得住——「run 突然停了」的观感就是从"什么都没留下"来的。 */
function applyRunFailed(items: TimelineItem[], data: Record<string, unknown>): TimelineItem[] {
  // `text` 是内核拼好的展示句（与落盘那条一字不差）；`message` 是原始原因，作为兜底
  const message = str(data.text) || str(data.message)
  if (!message) return items
  const entryId = str(data.entry_id) || null
  if (entryId !== null && items.some((item) => item.kind === 'error' && item.entryId === entryId)) return items
  return [...items, { kind: 'error', entryId, text: message }]
}

function applyUserMessage(items: TimelineItem[], event: TimelineEvent, data: Record<string, unknown>): TimelineItem[] {
  const entryId = str(data.entry_id)
  const message = data.message as MessagePayload | undefined
  const { text, images } = userContent(message?.content, entryId || null)
  if (!entryId || (!text && images.length === 0)) return items
  if (items.some((item) => item.kind === 'user' && item.entryId === entryId)) return items
  const next: TimelineItem = {
    kind: 'user',
    entryId,
    text,
    ...(images.length ? { images } : {}),
    ts: event.ts,
  }
  // 乐观气泡（发送时先画的那个）就地收编，不再多出一条。带图时按文字配对——
  // 事件里没有客户端 id，而「同一段文字 + 紧随其后」已经足够认出来（图片是随它发的）。
  // 待办输入落库：按 input_id 就地收编（那条段落本来就在列表里，位置也不该跳）
  const inputId = str(data.input_id)
  if (inputId) {
    const at = items.findIndex(
      (item) => item.kind === 'user' && item.pending?.inputId === inputId,
    )
    if (at >= 0) return replace(items, at, next)
  }
  const last = items.at(-1)
  if (last?.kind === 'user' && last.entryId === null && last.text === text) {
    return replace(items, items.length - 1, next)
  }
  return [...items, next]
}

function applyAssistantMessage(
  items: TimelineItem[],
  event: TimelineEvent,
  data: Record<string, unknown>,
): TimelineItem[] {
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
      out = replace(out, streaming, { ...item, entryId, text: text || item.text, streaming: false, ts: event.ts })
    }
  } else if (text.trim()) {
    out = [...out, { kind: 'assistant', entryId, text, streaming: false, ts: event.ts }]
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
  // 读数留给持久消息：这一段的正文与落库身份都以它为准，时间读数一起从它取。
  return [...out, { kind: 'assistant', entryId: null, text, streaming: true, ts: null }]
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
  return replace(items, at, closeRunStreams({ ...item, status, durationMs, result }))
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
    // 明细是 live-only：历史端（重读会话）没有它，运行端有——谁有给谁
    runs: run.runs.some((item) => item.items.length > 0) ? run.runs : base.runs,
  }
}

// ---------------------------------------------------------------- 一轮回话：分组与折叠

/**
 * 一轮回话（两条用户消息之间的全部段落）+ 它的折叠判定材料。
 *
 * 折叠的是一条回话里的**过程**：跑完的轮只留收尾正文，过程收成一行
 * （「已完成，用时 13分11秒」，点开还原）。判定全在这里，渲染层只照做——
 * 于是「过程」与「收尾」折出同一个形状，刷新前后也一样。
 */
export type TurnGroup = {
  /** 这一轮用户说的话；null = 窗口从半轮中间开始（更早的历史还没加载）。 */
  user: UserItem | null
  /** 本轮全部段落（含用户段），顺序不变——复制整段回话按它拼。 */
  items: TimelineItem[]
  /** 可折叠的过程：用户段与收尾正文之间的一切（思考 / 中间正文 / 工具）。 */
  process: TimelineItem[]
  /** 收尾正文：本轮**末段**且已落库的那段正文；null = 这一轮没有收尾，不折。 */
  answer: AnswerItem | null
  /** 本轮用时（收尾读数 − 用户读数）；缺读数或时钟倒挂时为 null。 */
  durationMs: number | null
  /** 组在扁平列表里的起点：live-only 段的 key 靠它保持全局唯一。 */
  offset: number
  /** 折叠开关的身份：取用户段（半轮的组取收尾段）的落库身份，归并与重放都不换 key。 */
  key: string
}

type UserItem = Extract<TimelineItem, { kind: 'user' }>
type AnswerItem = Extract<TimelineItem, { kind: 'assistant' }>

/** 收尾正文：必须是末段——末尾是工具就是中断/失败，那一轮没有「大幅消息」可留，整轮铺着。 */
function isAnswer(item: TimelineItem): item is AnswerItem {
  return item.kind === 'assistant' && item.entryId !== null && item.text.trim() !== ''
}

/** 段落列表 → 各轮分组。切点就是用户段：每条用户消息开启一轮，直到下一条用户消息。 */
export function turnGroups(items: TimelineItem[]): TurnGroup[] {
  const groups: TurnGroup[] = []
  let start = 0
  for (let end = 0; end <= items.length; end += 1) {
    if (end < items.length && items[end]!.kind !== 'user') continue
    const slice = items.slice(start, end)
    if (slice.length > 0) groups.push(makeGroup(slice, start))
    start = end
  }
  return groups
}

function makeGroup(items: TimelineItem[], offset: number): TurnGroup {
  const head = items[0]
  const user = head?.kind === 'user' ? head : null
  const tail = items.at(-1)
  const answer = tail !== undefined && isAnswer(tail) ? tail : null
  const from = user === null ? 0 : 1
  const to = answer === null ? items.length : items.length - 1
  const span = user !== null && user.ts !== null && answer !== null && answer.ts !== null ? answer.ts - user.ts : null
  return {
    user,
    items,
    process: items.slice(from, to),
    answer,
    durationMs: span !== null && span >= 0 ? span : null,
    offset,
    key: identity(user ?? answer ?? items[0]!) ?? `turn:${offset}`,
  }
}

// ---------------------------------------------------------------- 子运行：卡片折叠行与面板

/** 卡片折叠行要的子步骤：把每条子运行里的工具段按任务摊平（顺序就是它调用的顺序）。 */
export function subagentSteps(item: TimelineItem): SubagentStep[] {
  if (item.kind !== 'tool') return []
  const steps: SubagentStep[] = []
  for (const run of item.runs) {
    for (const child of run.items) {
      if (child.kind === 'tool') {
        steps.push({ task: run.task, callId: child.callId, name: child.name, args: child.args, status: child.status })
      }
    }
  }
  return steps
}

/** 面板要的一条子运行（扁平行）。 */
export type SubagentRunView = {
  /** 派发它的那次 subagent 调用：面板的 key 用它 + index。 */
  callId: string
  task: string
  index: number
  items: TimelineItem[]
  /** 这批还在跑：面板据此给运行中的标记；单条成败只在内核汇总的那段结果里。 */
  running: boolean
}

/**
 * 整条时间线里所有子运行，按出现顺序摊平——面板读的就是它。
 *
 * 明细（`items`）不落库，所以刷新后的历史会话里它恒为空（只剩任务清单）；
 * 这一点面板要照实说，不能画一个空的时间线假装"它什么都没干"。
 */
export function subagentRuns(items: TimelineItem[]): SubagentRunView[] {
  const views: SubagentRunView[] = []
  for (const item of items) {
    if (item.kind !== 'tool' || item.name !== 'subagent') continue
    for (const run of item.runs) {
      views.push({
        callId: item.callId,
        task: run.task,
        index: run.index,
        items: run.items,
        running: item.status === 'running',
      })
    }
  }
  return views
}
