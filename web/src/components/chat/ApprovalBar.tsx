/**
 * 审批条（报告 §7.9 的「确认栏」候选）：manual 模式下内核挂起等待人类裁决时，
 * 从输入区上方升起（hana-rise，clip-path 裁剪入场）。允许 = accent 实底，
 * 拒绝 = 发丝线描边；busy 时禁用（等待后端幂等确认）。多条待决纵向堆叠。
 */

import type { LiveApproval } from '../../state/useRunStream'
import { Icon } from '../../ui/Icon'

export type ApprovalBarProps = {
  approvals: LiveApproval[]
  busy: boolean
  onDecide: (approvalId: string, decision: 'allow' | 'deny') => void
}

export function ApprovalBar({ approvals, busy, onDecide }: ApprovalBarProps) {
  if (approvals.length === 0) return null

  return (
    <div className="flex flex-col gap-a8">
      {approvals.map((a) => (
        <div
          key={a.approvalId}
          className="rounded-md border-hairline border-hair bg-card p-a12 shadow-soft"
          style={{ animation: 'hana-rise var(--duration-slow) var(--ease-out)' }}
        >
          <div className="flex items-center gap-a8">
            <span className="shrink-0 text-accent">
              <Icon name="shield-check" size={14} />
            </span>
            <span className="font-ui text-ui font-medium text-ink">请求执行：{a.tool}</span>
          </div>
          {a.reason && <p className="mt-a4 font-ui text-hint text-ink-light">{a.reason}</p>}
          {a.arguments && (
            <p className="mt-a4 truncate font-mono text-micro text-ink-muted">{a.arguments}</p>
          )}
          <div className="mt-a8 flex items-center justify-end gap-a8">
            <button
              type="button"
              onClick={() => onDecide(a.approvalId, 'deny')}
              disabled={busy}
              className="rounded-sm border-hairline border-hair px-a12 py-a4 font-ui text-hint font-medium text-danger transition-colors duration-fast ease-out hover:bg-overlay-light disabled:cursor-not-allowed disabled:opacity-40"
            >
              拒绝
            </button>
            <button
              type="button"
              onClick={() => onDecide(a.approvalId, 'allow')}
              disabled={busy}
              className="rounded-sm bg-accent px-a12 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              允许
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
