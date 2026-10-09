/**
 * Pending decisions above the composer: destructive-command approvals and model questions.
 * The double confirmation for approvals (first "allow" expands a confirm card, the second
 * click sends `allow`; `deny` goes straight out) is a frontend discipline.
 */

import { useState } from 'react'

import type { LiveApproval } from '../../state/useRunStream'
import { Icon } from '../../ui/Icon'

export type ApprovalBarProps = {
  approvals: LiveApproval[]
  busy: boolean
  onDecide: (approvalId: string, decision: 'allow' | 'deny') => void
  /** Answer one question (option buttons and free text both land here). */
  onAnswer: (approvalId: string, text: string) => void
}

export function ApprovalBar({ approvals, busy, onDecide, onAnswer }: ApprovalBarProps) {
  // Approval id that passed the first "allow"; a queued approval clears it, so nothing dangles.
  const [confirming, setConfirming] = useState<string | null>(null)
  // Free-text drafts keyed by approval id, so concurrent questions do not share state.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  if (approvals.length === 0) return null
  const confirmingId =
    confirming !== null && approvals.some((a) => a.approvalId === confirming) ? confirming : null

  const question = (a: LiveApproval) => (
    <div key={a.approvalId} className="flex flex-col gap-a8">
      <div className="flex items-center gap-a8">
        <span className="shrink-0 text-accent">
          <Icon name="message-square" size={14} />
        </span>
        <span className="font-ui text-ui font-medium text-ink">问你一句</span>
      </div>
      <p className="font-ui text-ui leading-[1.7] text-ink">{a.reason}</p>
      <div className="flex flex-wrap gap-a8">
        {a.options.map((option) => (
          <button
            key={option}
            type="button"
            disabled={busy}
            onClick={() => onAnswer(a.approvalId, option)}
            className="rounded-sm border-hairline border-hair px-[11px] py-[4px] font-ui text-hint text-accent transition-colors duration-fast ease-out hover:bg-accent-light disabled:opacity-40"
          >
            {option}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-a8">
        <input
          value={drafts[a.approvalId] ?? ''}
          onChange={(e) => setDrafts((cur) => ({ ...cur, [a.approvalId]: e.target.value }))}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (drafts[a.approvalId] ?? '').trim()) {
              onAnswer(a.approvalId, (drafts[a.approvalId] ?? '').trim())
            }
          }}
          disabled={busy}
          placeholder={a.options.length > 0 ? '或者自己写一个答案…' : '输入回答…'}
          aria-label="回答"
          className="h-control min-w-0 flex-1 rounded-sm border-hairline border-hair bg-card px-a8 font-ui text-ui text-ink focus:border-accent focus:outline-none disabled:opacity-40"
        />
        <button
          type="button"
          disabled={busy || !(drafts[a.approvalId] ?? '').trim()}
          onClick={() => onAnswer(a.approvalId, (drafts[a.approvalId] ?? '').trim())}
          className="shrink-0 rounded-sm border-transparent bg-accent px-[15px] py-[6px] font-ui text-ui font-medium text-card transition-colors duration-fast ease-out disabled:opacity-40"
        >
          回答
        </button>
      </div>
    </div>
  )

  return (
    <div className="flex flex-col gap-a8">
      {approvals.map((a) =>
        a.kind === 'question' ? (
          <div
            key={a.approvalId}
            className="rounded-md border-hairline border-hair bg-card p-a12 shadow-soft"
            style={{ animation: 'hana-rise var(--duration-slow) var(--ease-out)' }}
          >
            {question(a)}
          </div>
        ) : (
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

          {confirmingId === a.approvalId ? (
            <div
              className="mt-a8 rounded-sm border-hairline bg-danger/[0.08] p-a8"
              style={{ animation: 'hana-fade-up var(--duration-fast) var(--ease-out)' }}
            >
              <p className="font-ui text-ui font-medium text-danger">确认执行</p>
              <p className="mt-a4 font-ui text-hint leading-[1.6] text-danger">
                毁灭级命令：确认后立即执行，本次运行不再重复询问。
              </p>
              <div className="mt-a8 flex items-center justify-end gap-a8">
                <button
                  type="button"
                  onClick={() => setConfirming(null)}
                  disabled={busy}
                  className="rounded-sm border-hairline border-hair px-a12 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light disabled:cursor-not-allowed disabled:opacity-40"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => onDecide(a.approvalId, 'allow')}
                  disabled={busy}
                  className="rounded-sm bg-accent px-a12 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
                >
                  确认执行
                </button>
              </div>
            </div>
          ) : (
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
                onClick={() => setConfirming(a.approvalId)}
                disabled={busy}
                className="rounded-sm bg-accent px-a12 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                允许
              </button>
            </div>
          )}
        </div>
        ),
      )}
    </div>
  )
}
