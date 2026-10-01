/*
 * 用量读数：输入区角落的一行。
 *
 * 为什么数字与细条挤在一行：这两件事回答的是同一个问题（"我的上下文还剩多少"），
 * 分成两处就得在两个地方各看一次。条只在有分块数据时才画——**没有数据 ≠ 占比 0**，
 * 画一条空的堆叠条等于告诉用户"三块都几乎没占"，那是假信息。
 *
 * 三段的颜色刻意低饱和且只有一档强调色（accent），另两段走墨灰与墨绿：
 * 报告 §9 的黑名单里"第二种主色"是硬禁区，这里用同色系的明度差来区分。
 */

import type { ReactElement } from 'react'

import type { UsageReport } from '../../../api/types'
import { Tooltip, cx } from '../../../ui/primitives'
import { compactUsage, contextSegments } from '../lib/usage'

export interface UsageMeterProps {
  usage: UsageReport | null
}

/** 三段的含义与颜色；顺序固定（系统 → 工具 → 对话），与内核 `_split_context` 一致。 */
const SEGMENT_STYLE: Record<'system' | 'tools' | 'messages', { label: string; className: string }> =
  {
    system: { label: '系统提示词', className: 'bg-ink-faint/60' },
    tools: { label: '工具定义', className: 'bg-accent' },
    messages: { label: '对话消息', className: 'bg-ok' },
  }

export function UsageMeter({ usage }: UsageMeterProps): ReactElement {
  const segments = contextSegments(usage)
  const label = compactUsage(usage)
  // 条本身没有文字，读屏只能靠这个标签；它同时是 Tooltip 的文案，
  // 于是"鼠标看到的"与"读屏听到的"是同一句话，不会各说一套。
  const summary =
    segments === null
      ? null
      : segments
          .map((segment) => {
            const style = SEGMENT_STYLE[segment.key]
            return `${style.label} ${Math.round(segment.ratio * 100)}%`
          })
          .join(' · ')

  return (
    <div className="flex items-center gap-a6 text-hint text-ink-muted">
      <span className="tabular-nums">{label}</span>
      {segments === null || summary === null ? null : (
        <Tooltip label={`上下文分块：${summary}`}>
          <span
            role="img"
            aria-label={`上下文分块：${summary}`}
            className="flex h-[3px] w-[72px] overflow-hidden rounded-xs bg-inset"
          >
            {segments.map((segment) => (
              <span
                key={segment.key}
                className={cx('h-full', SEGMENT_STYLE[segment.key].className)}
                // 占比是运行期数据，静态类名表达不了；这里只写宽度，
                // 颜色 / 圆角仍然全部来自 token 类名。
                style={{ width: `${(segment.ratio * 100).toFixed(2)}%` }}
              />
            ))}
          </span>
        </Tooltip>
      )}
    </div>
  )
}
