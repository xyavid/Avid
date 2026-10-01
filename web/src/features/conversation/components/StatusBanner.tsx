/**
 * 运行状态条：把 reducer 的 phase 翻译成一条安静的人类语言提示。
 *
 * 它只在"有话说"的时候出现——idle / finished 且没有错误、也没有断流时返回 null。
 * 一条常驻的"已完成"横条只是噪音：完成状态由正文本身与输入区的空闲形状表达。
 */
import type { ReactElement } from 'react'

import type { RunPhase } from '../../../events/reducer'
import { Button } from '../../../ui/primitives'
import { AlertTriangleIcon, InfoIcon, LoaderIcon } from '../../../ui/icons'

export interface StatusBannerProps {
  phase: RunPhase
  activity: string
  error: { code: string; message: string } | null
  detached: boolean
  onRetry?: () => void
}

const DETACHED_TEXT = '重连中，历史可能不完整'

function dangerRow(error: StatusBannerProps['error'], onRetry?: () => void): ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-a8 bg-danger-bg px-a12 py-a6 text-ui text-danger">
      <AlertTriangleIcon size={14} className="shrink-0" />
      <span className="min-w-0 flex-1">{error?.message ?? '运行失败'}</span>
      {error ? <span className="text-hint">{error.code}</span> : null}
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  )
}

function runningRow(activity: string): ReactElement {
  return (
    <div className="flex items-center gap-a8 bg-inset px-a12 py-a6 text-ui text-ink-light">
      <LoaderIcon size={14} className="shrink-0 animate-spin text-ink-muted" />
      <span className="shrink-0">正在运行</span>
      {/* activity 为空时只留阶段名——不要显示一个空的分隔符。 */}
      {activity.trim() !== '' ? (
        <span className="min-w-0 truncate text-ink-muted">{activity}</span>
      ) : null}
    </div>
  )
}

function cancelledRow(): ReactElement {
  return (
    <div className="flex items-center gap-a8 bg-warn-bg px-a12 py-a6 text-ui text-warn">
      <InfoIcon size={14} className="shrink-0" />
      <span>已取消</span>
    </div>
  )
}

export function StatusBanner({
  phase,
  activity,
  error,
  detached,
  onRetry,
}: StatusBannerProps): ReactElement | null {
  // 待审批由 ApprovalBar 负责：两个地方同时说"等你批准"，只会互相抢注意力，
  // 而且审批条才是能操作的那个。
  if (phase === 'awaiting_approval') return null

  let primary: ReactElement | null
  if (phase === 'failed') {
    // 失败但没带 error（理论上不该有）也要说"失败"，不能因为缺字段就把错误吞掉。
    primary = dangerRow(error, onRetry)
  } else if (phase === 'cancelled') {
    primary = cancelledRow()
  } else if (phase === 'running') {
    primary = runningRow(activity)
  } else {
    // idle / finished：只有带着错误才说话。错误不能被"已完成"盖掉。
    primary = error !== null ? dangerRow(error, onRetry) : null
  }

  if (primary === null && !detached) return null

  return (
    <section aria-label="运行状态" className="flex flex-col">
      {primary}
      {detached ? (
        <div className="px-a12 py-a2 text-hint text-ink-muted">{DETACHED_TEXT}</div>
      ) : null}
    </section>
  )
}
