/**
 * 消息流：把 durable 条目（OpenAI 形载荷）映射成冻结组件。
 *
 * 规则：
 * - user → UserBubble；
 * - assistant 带 tool_calls → 每个 tool_call 一张 ToolCard；后随 role:'tool'
 *   的结果按 tool_call_id 归位进卡身（不渲染成独立消息）；结果未到（中断批次）
 *   时卡身显示参数预览；
 * - assistant 纯文本 → AssistantMessage（衬线）；空内容的 tool_calls 轮次不渲染；
 * - notice 条目跳过——内核注入的提醒不是对话（与后端口径一致，见 test_web_api
 *   对 notice 线格式的钉子）。
 */

import type { ReactNode } from 'react'

import type { Entry } from '../../api/types'
import type { IconName } from '../../ui/Icon'
import { AssistantMessage } from './AssistantMessage'
import { ToolCard } from './ToolCard'
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

function toolIcon(name: string): IconName {
  return TOOL_ICONS[name] ?? 'file-frame'
}

function previewArgs(args: string): string {
  try {
    return JSON.stringify(JSON.parse(args), null, 2)
  } catch {
    return args
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
  const nodes: ReactNode[] = items.map((item, index) => {
    if (item.kind === 'user') return <UserBubble key={index}>{item.text}</UserBubble>
    if (item.kind === 'assistant') return <AssistantMessage key={index}>{item.text}</AssistantMessage>
    return (
      <ToolCard key={index} icon={toolIcon(item.call.name)} title={item.call.name}>
        <span className="line-clamp-6 block whitespace-pre-wrap">
          {item.call.result ?? previewArgs(item.call.args)}
        </span>
      </ToolCard>
    )
  })
  return <div className="flex flex-col gap-a16">{nodes}</div>
}
