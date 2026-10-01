/**
 * 消息流：把 durable 条目（OpenAI 形载荷）映射成冻结组件。
 *
 * 规则：
 * - user → UserBubble；
 * - assistant 带 tool_calls → 工具卡；**连续的工具调用聚成一组**（「N 个工具」，
 *   组头可开关整组）；role:'tool' 的结果按 tool_call_id 归位（结果首行做折叠
 *   行的预览，完整内容在展开态）；结果未到（中断批次）预览显示参数压缩串，
 *   状态 = 运行中；失败判定与后端 classify_tool_status 同口径（见 ToolCard）；
 * - assistant 纯文本 → AssistantMessage（衬线）；空内容的 tool_calls 轮次不渲染；
 * - notice 条目跳过——内核注入的提醒不是对话（与后端口径一致）。
 */

import type { ReactNode } from 'react'

import type { Entry } from '../../api/types'
import type { IconName } from '../../ui/Icon'
import { AssistantMessage } from './AssistantMessage'
import { ToolCard, type ToolStatus } from './ToolCard'
import { ToolGroup } from './ToolGroup'
import { UserBubble } from './UserBubble'

type ToolCall = { id: string; name: string; args: string; result: string | null }

type Item =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string }
  | { kind: 'tool'; call: ToolCall }

type MessagePayload = {
  role?: string
  content?: unknown
  tool_calls?: Array<{ id?: string; function?: { name?: string; arguments?: string } }>
  tool_call_id?: string
}

/** 工具名 → 图标；未登记的工具回落到通用文件帧。 */
const TOOL_ICONS: Record<string, IconName> = {
  bash: 'terminal',
  web_search: 'globe',
  subagent: 'git-branch',
}

/** 工具名 → 图标；未登记的工具回落到通用文件帧。页面活事件区也用它。 */
export function toolIcon(name: string): IconName {
  return TOOL_ICONS[name] ?? 'file-frame'
}

/** 与后端 classify_tool_status 同口径（schemas.py）。 */
const FAILED_PREFIXES = ['错误：', '参数错误：']
const FAILED_MARK = '执行失败：'

function statusOf(call: ToolCall): ToolStatus {
  if (call.result === null) return 'running'
  const failed =
    FAILED_PREFIXES.some((p) => call.result!.startsWith(p)) || call.result!.slice(0, 64).includes(FAILED_MARK)
  return failed ? 'failed' : 'ok'
}

/** 折叠行预览：有结果取首行；无结果取参数压缩串（单行 JSON）。 */
function previewOf(call: ToolCall): string {
  if (call.result !== null) return call.result.split('\n')[0] ?? ''
  try {
    return JSON.stringify(JSON.parse(call.args))
  } catch {
    return call.args
  }
}

function asItems(entries: Entry[]): Item[] {
  const items: Item[] = []
  const byCallId = new Map<string, ToolCall>()
  for (const e of entries) {
    if (e.type !== 'message' || !e.message) continue
    const m = e.message as MessagePayload
    if (m.role === 'user' && typeof m.content === 'string' && m.content) {
      items.push({ kind: 'user', text: m.content })
    } else if (m.role === 'assistant') {
      for (const c of m.tool_calls ?? []) {
        if (!c.id || !c.function?.name) continue
        const call: ToolCall = { id: c.id, name: c.function.name, args: c.function.arguments ?? '', result: null }
        byCallId.set(c.id, call)
        items.push({ kind: 'tool', call })
      }
      if (typeof m.content === 'string' && m.content.trim()) {
        items.push({ kind: 'assistant', text: m.content })
      }
    } else if (m.role === 'tool' && typeof m.tool_call_id === 'string') {
      const call = byCallId.get(m.tool_call_id)
      if (call) call.result = typeof m.content === 'string' ? m.content : JSON.stringify(m.content ?? '')
    }
  }
  return items
}

export function Timeline({ entries }: { entries: Entry[] }) {
  const items = asItems(entries)
  const nodes: ReactNode[] = []
  let index = 0
  while (index < items.length) {
    const item = items[index]!
    if (item.kind !== 'tool') {
      nodes.push(
        item.kind === 'user' ? (
          <UserBubble key={index}>{item.text}</UserBubble>
        ) : (
          <AssistantMessage key={index}>{item.text}</AssistantMessage>
        ),
      )
      index += 1
      continue
    }
    // 连续的工具调用聚成一组（哪怕只有一条：组头开关比单卡多一层，一条时直接给折叠行）
    const run: Array<Extract<Item, { kind: 'tool' }>> = []
    while (index < items.length && items[index]!.kind === 'tool') {
      run.push(items[index] as Extract<Item, { kind: 'tool' }>)
      index += 1
    }
    const cards = run.map((t, j) => (
      <ToolCard
        key={`${index}-${j}`}
        icon={toolIcon(t.call.name)}
        title={t.call.name}
        preview={previewOf(t.call)}
        status={statusOf(t.call)}
      >
        <span className="line-clamp-6 block whitespace-pre-wrap">{t.call.result ?? t.call.args}</span>
      </ToolCard>
    ))
    nodes.push(run.length === 1 ? cards[0]! : <ToolGroup key={`group-${index}`} count={run.length}>{cards}</ToolGroup>)
  }
  return <div className="flex flex-col gap-a16">{nodes}</div>
}
