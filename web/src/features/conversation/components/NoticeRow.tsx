/**
 * 时间线上的"插播"行：上下文压缩、TODO 更新、停止提示、一般提示。
 *
 * 为什么单独成行而不是塞进消息气泡：它不是谁说的话，而是运行过程本身发生的事。
 * 整行用最小的字号与最淡的墨色，**左对齐与正文同一列**（一致比好看重要：
 * 一行居中、一行靠左会让读者每次都要重新找列）。
 */
import type { ReactElement } from 'react'

import type { TimelineEntry } from '../../../events/reducer'
import { InfoIcon, SparkIcon, WrenchIcon } from '../../../ui/icons'

export interface NoticeRowProps {
  entry: TimelineEntry
}

type NoticeKind = NonNullable<TimelineEntry['notice']>

const NOTICE_LABELS: Record<NoticeKind, string> = {
  compaction: '上下文已压缩',
  todo: '任务清单已更新',
  nudge: '继续推进',
  info: '提示',
}

function noticeIcon(kind: NoticeKind): ReactElement {
  // 图标是纯装饰（默认 aria-hidden）：语义已经由旁边的文字说清楚了。
  switch (kind) {
    case 'compaction':
      return <InfoIcon size={14} className="mt-a2 shrink-0" />
    case 'todo':
      return <WrenchIcon size={14} className="mt-a2 shrink-0" />
    case 'nudge':
      return <SparkIcon size={14} className="mt-a2 shrink-0" />
    case 'info':
      return <InfoIcon size={14} className="mt-a2 shrink-0" />
  }
}

export function NoticeRow({ entry }: NoticeRowProps): ReactElement {
  // notice 档位缺省当 info：宁可少一个更精确的图标，也不要这一行整条不显示。
  const kind: NoticeKind = entry.notice ?? 'info'

  return (
    // 只画上边一条发丝线（`border-t-hair` 同时给线宽与线色，见下方报告的那条 Tailwind 坑）。
    <div className="flex items-start gap-a6 border-t-hair py-a6 pl-a8 text-hint text-ink-muted">
      {noticeIcon(kind)}
      <span className="min-w-0 flex-1">
        <span>{NOTICE_LABELS[kind]}</span>
        {entry.text !== '' ? <span className="ml-a6">{entry.text}</span> : null}
      </span>
    </div>
  )
}
