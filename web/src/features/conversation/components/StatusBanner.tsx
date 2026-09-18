import { Badge, Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useErrorText } from '../../../lib/errors'
import type { RunPhase } from '../../../events/reducer'

export interface StatusBannerProps {
  phase: RunPhase
  error: { code: string; message: string } | null
  degraded: boolean
  reconnectAttempt: number | null
  detached: boolean
  truncatedTail: boolean
  onRefetch: () => void
}

/**
 * 状态提示条：降级（轮询）、重连、视图待重建、链尾中断、运行失败。
 * 「运行失败」是唯一需要用户行动的失败；取消不是错误，不在这里出现。
 */
export function StatusBanner(props: StatusBannerProps) {
  const { t } = useTranslation()
  const errorText = useErrorText()

  const notes: string[] = []
  if (props.degraded) notes.push(t('chat.degraded'))
  if (props.reconnectAttempt !== null) {
    notes.push(t('chat.reconnect', { attempt: props.reconnectAttempt }))
  }
  if (props.detached) notes.push(t('chat.error.detached'))
  if (props.truncatedTail) notes.push(t('chat.truncatedTail'))
  if (props.phase === 'failed' && props.error) {
    notes.push(errorText(props.error.code, props.error.message))
  }

  if (notes.length === 0) return null

  const failed = props.phase === 'failed'

  return (
    <div
      role={failed ? 'alert' : 'status'}
      className={`mx-3 mt-2 flex flex-wrap items-center gap-2 rounded-chip border-hair border-ink px-3 py-2 text-xs ${
        failed ? 'bg-danger-bg/30' : 'bg-warn-bg/30'
      }`}
    >
      <Badge tone={failed ? 'danger' : 'warn'}>{t('errors.title')}</Badge>
      {notes.map((note) => (
        <span key={note}>{note}</span>
      ))}
      <Button size="sm" className="ml-auto" onClick={props.onRefetch}>
        {t('common.refresh')}
      </Button>
    </div>
  )
}
