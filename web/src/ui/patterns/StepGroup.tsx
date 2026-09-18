/**
 * 连续工具调用成组：折叠态只说「连续 N 次工具调用」，展开态是完整的调用序列。
 *
 * 失败/拒绝的调用不进组（它们各自需要被单独看见），所以组件内按 status 再过滤一次：
 * 调用方的分组规则可能已经过期，这里兜底。少于 `EVENT_GROUP_MIN_SIZE` 时默认展开——
 * 两条调用还要再点一次才能看见，纯粹是浪费。
 */
import { useState } from 'react'

import { useTranslation } from '../../lib/i18n'
import type { ToolRun } from '../../events/reducer'
import type { Density } from '../../state/uiStore'
import { Badge, Button } from '../../ui/primitives'
import { ToolCallCard } from './ToolCallCard'

export const EVENT_GROUP_MIN_SIZE = 2

export interface StepGroupProps {
  onInspect?: (run: ToolRun) => void
  title: string
  tools: ToolRun[]
  /** toolCallId → 工具输出全文；缺省时卡片只显示状态与参数。 */
  contents?: Record<string, string>
  density?: Density
}

export function StepGroup({
  title,
  tools,
  contents,
  density = 'comfy',
  onInspect,
}: StepGroupProps) {
  const { t } = useTranslation()
  const visible = tools.filter((run) => run.status !== 'failed' && run.status !== 'denied')
  const [open, setOpen] = useState(visible.length < EVENT_GROUP_MIN_SIZE)
  if (visible.length === 0) return null

  const label = t('tools.group.title', { count: visible.length })
  // 调用方常常直接把「连续 N 次工具调用」当 title 传进来；两者相同时只显示一次。
  const head = title && title !== label ? title : ''
  if (!open) {
    return (
      <Button size="sm" variant="secondary" className="w-fit" aria-expanded={false} onClick={() => setOpen(true)}>
        {head ? <span className="rounded-sketch-1 bg-mark/40 px-1 font-sketch text-xs">{head}</span> : null}
        {label}
      </Button>
    )
  }
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        {head ? <Badge tone="mark">{head}</Badge> : null}
        <span className="font-sketch text-xs">{label}</span>
        <Button size="sm" variant="secondary" className="ml-auto" aria-expanded onClick={() => setOpen(false)}>
          {t('common.collapse')}
        </Button>
      </div>
      {visible.map((run) => (
        <ToolCallCard
          key={run.toolCallId}
          run={run}
          content={contents?.[run.toolCallId]}
          density={density}
          onInspect={onInspect}
        />
      ))}
    </section>
  )
}
