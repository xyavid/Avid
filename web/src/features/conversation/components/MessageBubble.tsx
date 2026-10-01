/**
 * 一条消息气泡（user / assistant）。
 *
 * 视觉取舍（hana 报告 §7.3）：用户组右对齐、给一张纸色底；assistant 组左对齐、
 * **无底无边框**，只靠 8px 左内边距与正文列对齐——气泡一多，方框会把对话切成表格。
 *
 * 正文一律当**纯文本**渲染并保留换行（`whitespace-pre-wrap`）：
 * 本次范围明确不引入 markdown 渲染库，宁可少一层富文本，也不为它背一个依赖。
 */
import type { ReactElement } from 'react'

import type { TimelineEntry } from '../../../events/reducer'
import { Tooltip } from '../../../ui/primitives'
import { relativeTime } from '../lib/relativeTime'

export interface MessageBubbleProps {
  entry: TimelineEntry
  /** 覆盖 `entry.streaming`（父级更清楚"这一轮还在流"） */
  streaming?: boolean
  /** "现在"；不传时只显示绝对时间。组件内不读 `Date.now()`，否则用例与快照都不稳定 */
  now?: number
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** 完整绝对时间，给 Tooltip 用。 */
function absoluteTime(ts: number): string {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 正文旁的紧凑绝对时间（时:分）。 */
function clockTime(ts: number): string {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return '—'
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function MessageBubble({ entry, streaming, now }: MessageBubbleProps): ReactElement | null {
  // tool / notice 有各自的渲染器；这里只管两种消息气泡，别的形状直接不画。
  if (entry.kind !== 'user' && entry.kind !== 'assistant') return null

  const isStreaming = streaming ?? entry.streaming === true
  // 传了 now 才给相对时间；没传就退回绝对时间，避免出现"刚刚"这种没有参照物的说法。
  const meta = now === undefined ? clockTime(entry.ts) : relativeTime(entry.ts, now)
  const absolute = absoluteTime(entry.ts)

  if (entry.kind === 'user') {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[85%] items-end gap-a8">
          <Tooltip label={absolute}>
            <span className="shrink-0 text-hint text-ink-faint">{meta}</span>
          </Tooltip>
          <div className="whitespace-pre-wrap rounded-sm bg-user-bubble px-a12 py-a8 text-body text-ink">
            {entry.text}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-end gap-a8">
      <div className="avid-prose max-w-chat-col whitespace-pre-wrap pl-a8 text-body text-ink">
        {entry.text}
        {isStreaming ? (
          // 4px 呼吸点：只动透明度不动位移（§8.2 禁弹跳），比闪烁光标安静得多。
          <span
            aria-hidden="true"
            className="ml-a6 inline-block h-a4 w-a4 animate-pulse rounded-full bg-ink-faint align-middle"
          />
        ) : null}
      </div>
      <Tooltip label={absolute}>
        <span className="shrink-0 text-hint text-ink-faint">{meta}</span>
      </Tooltip>
    </div>
  )
}
