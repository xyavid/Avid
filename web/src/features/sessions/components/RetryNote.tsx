/**
 * 「一句话 + 重试」：导航列里两处取数失败（工作区列表、会话列表）用的是同一块形状，
 * 只有文案与重试的目标不同。原先它在 `SessionList` 里逐字写了两遍。
 */
import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'

export interface RetryNoteProps {
  message: string
  onRetry: () => void
}

export function RetryNote({ message, onRetry }: RetryNoteProps) {
  const { t } = useTranslation()

  return (
    <div className="empty-note">
      <p>{message}</p>
      <Button size="sm" className="mt-2" onClick={onRetry}>
        {t('common.retry')}
      </Button>
    </div>
  )
}
