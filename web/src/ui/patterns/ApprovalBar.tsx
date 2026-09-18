/**
 * 审批卡：一次权限请求就是一个决定，所以只出两个按钮，不做中间态。
 *
 * 三条取舍：
 *   · **拒绝是默认焦点**（`autoFocus`）——审批是打断，默认动作应当是不放行；
 *   · **Enter = 允许、Escape = 拒绝**写在卡片上，并对 Enter 做 `preventDefault`：否则
 *     焦点在拒绝按钮上按 Enter 会先触发按钮自身的 click，同一个键就有两种含义；
 *   · 过期、已决、busy 三种情况一律禁用按钮：重复提交由服务端按 approvalId 幂等兜底，
 *     但前端不该主动制造第二次请求。
 */
import { useState } from 'react'
import type { KeyboardEvent } from 'react'

import { useTranslation } from '../../lib/i18n'
import type { ApprovalRequest } from '../../events/reducer'
import { Badge, Button } from '../../ui/primitives'

export interface ApprovalBarProps {
  approval: ApprovalRequest
  busy?: boolean
  onAnswer: (decision: 'allow' | 'deny') => void
}

/** 相对时间交给 Intl：只有 `approvals.expires` 一句文案需要翻译。 */
export function relativeTime(ms: number, locale: string): string {
  const format = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  const seconds = Math.round(ms / 1000)
  if (Math.abs(seconds) < 60) return format.format(seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return format.format(minutes, 'minute')
  return format.format(Math.round(minutes / 60), 'hour')
}

function Facts({ approval, open, expired, decided }: {
  approval: ApprovalRequest; open: boolean; expired: boolean; decided: boolean
}) {
  const { t } = useTranslation()
  return (
    <>
      {open ? (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-ink/70">{t('approvals.reason', { reason: approval.reason })}</p>
          <pre aria-label={t('approvals.toolArguments')}
            className="term scroll-area max-h-48 overflow-x-auto rounded-sketch-1 p-2">
            {JSON.stringify(approval.arguments, null, 2)}
          </pre>
        </div>
      ) : null}
      {expired ? <p role="alert" className="text-xs text-danger">{t('approvals.expired')}</p> : null}
      {decided ? (
        <p className="text-xs text-ink/70">
          {approval.decision === 'allow' ? t('approvals.answered.allow') : t('approvals.answered.deny')}
          {approval.resolvedReason ? ` · ${approval.resolvedReason}` : ''}
        </p>
      ) : null}
    </>
  )
}

function AnswerButtons({ locked, onAnswer }: {
  locked: boolean; onAnswer: (decision: 'allow' | 'deny') => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button autoFocus variant="danger" disabled={locked} onClick={() => onAnswer('deny')}>
        {t('approvals.deny')}
      </Button>
      <Button variant="primary" disabled={locked} onClick={() => onAnswer('allow')}>
        {t('approvals.allow')}
      </Button>
      <span className="text-xs text-ink/70">{t('approvals.defaultDeny')}</span>
    </div>
  )
}

export function ApprovalBar({ approval, busy = false, onAnswer }: ApprovalBarProps) {
  const { t, locale } = useTranslation()
  const [open, setOpen] = useState(true)
  const decided = approval.decision !== null
  const expired = !decided && approval.expiresAt > 0 && approval.expiresAt < Date.now()
  const locked = busy || decided || expired
  const answer = (decision: 'allow' | 'deny') => {
    if (!locked) onAnswer(decision)
  }
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (locked) return
    if (event.key === 'Escape') {
      event.preventDefault()
      answer('deny')
    } else if (event.key === 'Enter') {
      event.preventDefault()
      answer('allow')
    }
  }
  return (
    <div className="sketch-card flex flex-col gap-2 p-3" onKeyDown={onKeyDown}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={decided ? (approval.decision === 'allow' ? 'ok' : 'danger') : 'warn'}>
          {t('approvals.title')}
        </Badge>
        <span className="rounded-sketch-1 bg-mark/40 px-2 py-0.5 font-mono text-xs">{approval.tool}</span>
        {approval.expiresAt > 0 ? (
          <span className="font-mono text-xs text-ink/70">
            {t('approvals.expires', { time: relativeTime(approval.expiresAt - Date.now(), locale) })}
          </span>
        ) : null}
        <Button size="sm" variant="ghost" className="ml-auto" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? t('common.collapse') : t('common.expand')}
        </Button>
      </div>
      <Facts approval={approval} open={open} expired={expired} decided={decided} />
      <AnswerButtons locked={locked} onAnswer={answer} />
    </div>
  )
}
