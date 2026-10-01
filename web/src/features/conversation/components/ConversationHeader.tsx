/**
 * 对话区顶栏：会话名 + 分支选择 + 用量读数。
 *
 * 44px 一行，底部一条发丝线（`border-b-hair` 同时给线宽与线色）。
 * 用量读数统一走 `lib/usage.ts` 的格式化函数：读数口径只允许有一个来源，
 * 否则"这个数是多少"会随出现的位置而变。
 */
import type { ReactElement } from 'react'

import type { Branch, UsageReport } from '../../../api/types'
import { Tooltip } from '../../../ui/primitives'
import { formatCache, formatUsage } from '../lib/usage'

export interface ConversationHeaderProps {
  sessionName: string
  sessionId: string | null
  branch: string
  branches: Branch[]
  onSelectBranch?: (name: string) => void
  usage: UsageReport | null
  round: number
  tokens: number
}

/** 千分位固定用 en-US：跟随运行环境 locale 会让同一个数字在不同机器上长得不一样。 */
function formatCount(value: number): string {
  return value.toLocaleString('en-US')
}

export function ConversationHeader({
  sessionName,
  sessionId,
  branch,
  branches,
  onSelectBranch,
  usage,
  round,
  tokens,
}: ConversationHeaderProps): ReactElement {
  const formatted = formatUsage(usage)
  // usage 快照还没到、但运行的 token 计数已经有数时，就把这个数说清楚：
  // "暂时没有用量快照"与"没有用量"是两回事，前者不该显示成「—」。
  const usageText = usage === null && tokens > 0 ? `${formatCount(tokens)} tok` : formatted
  const cacheText = formatCache(usage)

  return (
    <header className="flex h-[44px] shrink-0 items-center gap-a12 border-b-hair px-a16">
      <Tooltip label={sessionId ?? '未落盘的会话（没有会话 id）'}>
        <h1 className="min-w-0 truncate font-medium text-title text-ink">{sessionName}</h1>
      </Tooltip>
      {/* 只有一条分支时选择器没有可选项，藏起来比放一个死控件诚实。 */}
      {branches.length >= 2 ? (
        <select
          aria-label="分支"
          value={branch}
          onChange={(event) => onSelectBranch?.(event.target.value)}
          className="shrink-0 rounded-sm bg-inset px-a6 py-a2 text-caption text-ink-light"
        >
          {branches.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      ) : null}
      <div className="ml-auto flex shrink-0 items-center gap-a8 text-hint text-ink-faint tabular-nums">
        <span>第 {round} 轮</span>
        <span>{usageText}</span>
        {cacheText !== null ? <span>{cacheText}</span> : null}
      </div>
    </header>
  )
}
