/**
 * One-line summary of a finished turn that toggles its process rows; the duration comes from
 * the message timestamps (`MessageTs` in `state/timeline.ts`), and a missing reading says
 * "done" instead of inventing a number.
 */

import { elapsedLabel } from '../../ui/duration'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type TurnSummaryProps = {
  /** Turn duration; null = no reading. */
  durationMs: number | null
  open: boolean
  onToggle: () => void
}

export function TurnSummary({ durationMs, open, onToggle }: TurnSummaryProps) {
  const label = durationMs === null ? '已完成' : `已完成，用时 ${elapsedLabel(durationMs)}`

  return (
    <button
      type="button"
      aria-expanded={open}
      data-item="summary"
      onClick={onToggle}
      className="group flex w-full items-center gap-a6 border-b border-hair pb-a6 text-left"
    >
      <span
        className={cx(
          'font-ui text-hint transition-colors duration-fast ease-out',
          open ? 'text-ink-light' : 'text-ink-muted group-hover:text-ink-light',
        )}
      >
        {label}
      </span>
      <span
        className={cx(
          'text-ink-muted transition-transform duration-fast ease-out group-hover:text-ink-light',
          open ? 'rotate-180' : undefined,
        )}
      >
        <Icon name="chevron-down" size={12} />
      </span>
    </button>
  )
}
