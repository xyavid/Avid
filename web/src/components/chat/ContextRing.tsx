/**
 * Context-capacity ring in the composer's left cluster, with a click-open breakdown.
 * Readings come from the `run_status` / `run_finished` usage snapshots (per-round while
 * running, the persisted one after); a missing reading shows "—" and an empty track — not
 * reported is not the same as 0 — and the three parts are all the kernel accounts for
 * (`_split_context` in `agent/state.py`).
 */

import { useState } from 'react'

import type { UsageReport } from '../../api/types'
import { cx } from '../../ui/cx'

/** Utilization above which the ring turns danger. */
const DANGER_AT = 0.85

const RING_SIZE = 20
const RING_STROKE = 3

function ratioOf(usage: UsageReport | null): number | null {
  const ratio = usage?.context.utilization
  return ratio === null || ratio === undefined ? null : Math.min(1, Math.max(0, ratio))
}

function Ring({ ratio }: { ratio: number | null }) {
  const radius = (RING_SIZE - RING_STROKE) / 2
  const circumference = 2 * Math.PI * radius
  const filled = (ratio ?? 0) * circumference
  return (
    <svg
      width={RING_SIZE}
      height={RING_SIZE}
      viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
      aria-hidden
      className="-rotate-90"
    >
      <circle
        cx={RING_SIZE / 2}
        cy={RING_SIZE / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeOpacity={0.2}
        strokeWidth={RING_STROKE}
      />
      <circle
        cx={RING_SIZE / 2}
        cy={RING_SIZE / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth={RING_STROKE}
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference}`}
        className="transition-[stroke-dasharray] duration-slow ease-out"
      />
    </svg>
  )
}

function compactTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value >= 10_000) {
    const wan = value / 10_000
    return `${Number.isInteger(wan) ? wan.toFixed(0) : wan.toFixed(1)}万`
  }
  return value.toLocaleString('en-US')
}

function pct(ratio: number | null | undefined): string {
  return ratio === null || ratio === undefined ? '—' : `${(ratio * 100).toFixed(1)}%`
}

export type ContextRingProps = {
  usage: UsageReport | null
}

export function ContextRing({ usage }: ContextRingProps) {
  const [open, setOpen] = useState(false)
  const ratio = ratioOf(usage)
  const hot = ratio !== null && ratio >= DANGER_AT
  const context = usage?.context ?? null
  const parts = context?.parts ?? null
  // Parts describe the current prompt, so shares are of used tokens, not the window.
  const share = (value: number): string =>
    context?.tokens ? `${((value / context.tokens) * 100).toFixed(1)}%` : '—'

  return (
    <div className="relative">
      <button
        type="button"
        aria-label={`上下文容量 ${pct(ratio)}`}
        aria-expanded={open}
        title={`上下文容量 ${pct(ratio)}`}
        onClick={() => setOpen((value) => !value)}
        className={cx(
          'inline-flex h-[26px] w-[26px] items-center justify-center rounded-sm transition-colors duration-fast ease-out hover:bg-overlay-light focus-visible:bg-overlay-light focus:outline-none',
          hot ? 'text-danger' : 'text-ink-light',
        )}
      >
        <Ring ratio={ratio} />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="上下文容量"
          className="absolute bottom-full left-0 z-20 mb-a8 w-[320px] rounded-md border-hairline border-hair bg-card p-a12 shadow-soft"
        >
          <div className="flex items-baseline justify-between gap-a8">
            <span className="font-ui text-ui text-ink">上下文容量</span>
            <span className="truncate font-ui text-hint text-ink-muted">
              {compactTokens(context?.tokens)}/{compactTokens(context?.window)}（{pct(ratio)}）
            </span>
          </div>

          <div className="mt-a8 h-[6px] overflow-hidden rounded-full bg-overlay-medium">
            <div
              className={cx(
                'h-full rounded-full transition-[width] duration-slow ease-out',
                hot ? 'bg-danger' : 'bg-accent',
              )}
              style={{ width: `${(ratio ?? 0) * 100}%` }}
            />
          </div>

          <div className="mt-a12 flex flex-col gap-a6">
            <PartRow label="消息" value={parts === null ? '—' : share(parts.messages)} />
            <PartRow label="系统工具" value={parts === null ? '—' : share(parts.tools)} />
            <PartRow label="系统提示词" value={parts === null ? '—' : share(parts.system)} />
          </div>

          <div className="mt-a12 flex items-baseline justify-between gap-a8 border-t border-hair pt-a8">
            <span className="font-ui text-ui text-ink">平均缓存命中率</span>
            <span className="font-ui text-ui text-ink">{pct(usage?.cache.hit_ratio)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

function PartRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-a8">
      <span aria-hidden className="h-[6px] w-[6px] shrink-0 rounded-full bg-accent" />
      <span className="font-ui text-hint text-ink">{label}</span>
      <span className="ml-auto font-ui text-hint text-ink-muted tabular-nums">{value}</span>
    </div>
  )
}
