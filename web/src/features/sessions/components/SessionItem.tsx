import { Badge, Button, Input } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { SessionSummary } from '../../../api/types'
import { formatRelative } from '../lib/relativeTime'

export interface SessionItemProps {
  session: SessionSummary
  active: boolean
  editing: boolean
  name: string
  busy: boolean
  onSelect: () => void
  onStartRename: () => void
  onNameChange: (value: string) => void
  onSubmitRename: () => void
  onRequestDelete: () => void
}

/**
 * 会话行：一行两段——名字在左、相对时间在右（工作区已经是它的父文件夹，不再重复显示
 * 归属，也不再显示会话 id：那些信息在详情页与检查器里）。
 *
 * 标题本身是个 `Button`（保留墨线方框，与「改名 / 删除」同族），并且带
 * `aria-current` —— 既是"当前会话"的语义标记，也是 e2e 定位它的稳定锚点。
 */
export function SessionItem({
  session,
  active,
  editing,
  name,
  busy,
  onSelect,
  onStartRename,
  onNameChange,
  onSubmitRename,
  onRequestDelete,
}: SessionItemProps) {
  const { t, locale } = useTranslation()
  const relative = formatRelative(session.created_at, Date.now(), locale)
  // 逐档取词条（而不是拼 `sessions.time.${unit}`）：拼出来的键绕过 check-style 的
  // i18n 完整性检查，漏词条要到运行时才看得见。
  const timeLabel =
    relative.unit === 'now'
      ? t('sessions.time.now')
      : relative.unit === 'minute'
        ? t('sessions.time.minute', { count: relative.value })
        : relative.unit === 'hour'
          ? t('sessions.time.hour', { count: relative.value })
          : relative.unit === 'day'
            ? t('sessions.time.day', { count: relative.value })
            : relative.text

  return (
    <div
      className={`sketch-chip press press-2 flex flex-col gap-1 p-2 ${
        active ? 'bg-mark/40' : 'bg-card'
      }`}
    >
      {editing ? (
        <form
          className="flex gap-1"
          onSubmit={(event) => {
            event.preventDefault()
            onSubmitRename()
          }}
        >
          <Input
            aria-label={t('sessions.namePlaceholder')}
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
          />
          <Button size="sm" type="submit" loading={busy}>
            {t('common.save')}
          </Button>
        </form>
      ) : (
        <Button
          variant="secondary"
          className="h-auto w-full justify-between gap-2 px-2 py-1 text-left"
          onClick={onSelect}
          aria-current={active ? 'true' : undefined}
        >
          <span className="truncate font-sketch text-sm">
            {session.name ?? t('sessions.unnamed')}
          </span>
          <span className="shrink-0 font-mono text-[10px] text-ink/70">{timeLabel}</span>
        </Button>
      )}

      <div className="flex items-center justify-between gap-1">
        <Badge tone="neutral" count={session.message_count}>
          {t('sessions.count', { count: session.message_count })}
        </Badge>
        {session.active_run_id ? (
          <Badge tone="info" pulse>
            {t('sessions.active')}
          </Badge>
        ) : null}
      </div>

      <div className="flex gap-1">
        <Button size="sm" onClick={onStartRename}>
          {t('common.rename')}
        </Button>
        <Button size="sm" variant="secondary" onClick={onRequestDelete}>
          {t('common.delete')}
        </Button>
      </div>
    </div>
  )
}
