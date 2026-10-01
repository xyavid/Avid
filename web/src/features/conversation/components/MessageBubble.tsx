/**
 * 一条消息气泡（user / assistant）。
 *
 * 视觉取舍（hana 报告 §7.3）：用户组右对齐、给一张纸色底；assistant 组左对齐、
 * **无底无边框**，只靠 8px 左内边距与正文列对齐——气泡一多，方框会把对话切成表格。
 *
 * 正文渲染分两种，这是**有意的区分**，不是漏改：
 *   · assistant 走 `<Markdown>`：模型输出本来就是 markdown，标题 / 列表 / 行内 code
 *     该成为结构元素，而不是让读者在脑子里解析 `##` 与 `-`；
 *   · user 保持纯文本 + `whitespace-pre-wrap`：用户打进来的 `*` / `#` 就该原样显示，
 *     把用户的话当 markdown 解析等于替他"翻译"一遍输入，还可能把一行 `- - -`
 *     变成分割线——那是对输入内容的篡改。
 */
import type { ReactElement } from 'react'

import type { TimelineEntry } from '../../../events/reducer'
import { Markdown } from '../../../ui/markdown'
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
      {/*
        `avid-prose` 留在外层：它管行高（中文长文 1.75）与断词，markdown 的**结构**
        由 Markdown 自己负责，两者不重叠。
        流式呼吸点改成 flex 里的末项（`items-end` 让它贴住正文底边）：markdown 渲染出的
        是块级元素，把点写在块后面会掉到下一行去。点只动透明度不动位移（§8.2 禁弹跳）。
      */}
      <div className="avid-prose flex max-w-chat-col items-end gap-a6 pl-a8 text-body text-ink">
        <div className="min-w-0 flex-1">
          <Markdown source={entry.text} streaming={isStreaming} />
        </div>
        {isStreaming ? (
          <span
            aria-hidden="true"
            className="mb-a6 h-a4 w-a4 shrink-0 animate-pulse rounded-full bg-ink-faint"
          />
        ) : null}
      </div>
      <Tooltip label={absolute}>
        <span className="shrink-0 text-hint text-ink-faint">{meta}</span>
      </Tooltip>
    </div>
  )
}
