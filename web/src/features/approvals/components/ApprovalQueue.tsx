import { Badge } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { ApprovalBar } from '../../../ui/patterns'
import type { ApprovalRequest } from '../../../events/reducer'

export interface ApprovalQueueProps {
  approvals: ApprovalRequest[]
  busy?: boolean
  onAnswer: (approvalId: string, decision: 'allow' | 'deny') => void
}

/**
 * 审批队列：同一 run 内可能有多个待决项（subagent 最多 4 个并行），所以按队列
 * 呈现并显示剩余数量，不再有「终端只有一个」的假设。未答复一律收敛为拒绝。
 */
export function ApprovalQueue({ approvals, busy = false, onAnswer }: ApprovalQueueProps) {
  const { t } = useTranslation()
  const pending = approvals.filter((item) => item.decision === null)
  const resolved = approvals.filter((item) => item.decision !== null)

  return (
    <section className="flex flex-col gap-2" aria-label={t('approvals.title')}>
      <header className="flex items-center gap-2">
        <h3 className="font-sketch text-sm">{t('approvals.title')}</h3>
        <Badge tone={pending.length ? 'warn' : 'ok'} count={pending.length} pulse={pending.length > 0}>
          {pending.length ? t('approvals.pending', { count: pending.length }) : t('approvals.none')}
        </Badge>
        <span className="ml-auto text-[11px] text-ink/70">{t('approvals.defaultDeny')}</span>
      </header>

      {pending.length === 0 && resolved.length === 0 ? (
        <p className="empty-note">{t('approvals.none')}</p>
      ) : null}

      {pending.map((approval) => (
        <ApprovalBar
          key={approval.approvalId}
          approval={approval}
          busy={busy}
          onAnswer={(decision: 'allow' | 'deny') => onAnswer(approval.approvalId, decision)}
        />
      ))}

      {resolved.length > 0 ? (
        <details className="text-xs">
          <summary className="cursor-pointer font-sketch">
            {t('approvals.answered.allow')} / {t('approvals.answered.deny')}（{resolved.length}）
          </summary>
          <ul className="mt-2 space-y-1">
            {resolved.map((approval) => (
              <li key={approval.approvalId} className="flex items-center gap-2">
                <Badge tone={approval.decision === 'allow' ? 'ok' : 'danger'}>
                  {approval.decision === 'allow'
                    ? t('approvals.answered.allow')
                    : t('approvals.answered.deny')}
                </Badge>
                <span className="font-mono">{approval.tool}</span>
                <span className="text-ink/70">{approval.resolvedReason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  )
}
