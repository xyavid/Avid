import { Badge, Button, Input } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import type { SessionSummary } from '../../../api/types'

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

/** 会话卡：形状按索引轮换（相邻同级卡片不同形）。 */
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
          variant="ghost"
          className="h-auto flex-col items-start px-2 py-1 text-left"
          onClick={onSelect}
          aria-current={active ? 'true' : undefined}
        >
          <span className="font-sketch text-sm">
            {session.name ?? t('sessions.unnamed')}
          </span>
          <span className="font-mono text-[10px] text-ink/70">
            {session.id.slice(0, 8)} ·{' '}
            {new Date(session.created_at).toLocaleString(locale, { hour12: false })}
          </span>
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
