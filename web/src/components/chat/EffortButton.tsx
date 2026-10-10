/**
 * Reasoning-effort chip in the composer: picks what the effort parameter carries, from the levels
 * the selected model declares itself (`reasoning_efforts`), plus an unset option that keeps the
 * parameter out of the request. Width follows the level name — those strings are provider-defined
 * and vary in length — and nothing renders when the model declares no levels.
 */

import { useState } from 'react'

import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type EffortButtonProps = {
  /** Current level; null = unset, so the request carries no such parameter. */
  effort: string | null
  onChange: (effort: string | null) => void
  /** Levels declared by the selected model; empty means the model says nothing about effort. */
  levels: string[]
}

export function EffortButton({ effort, onChange, levels }: EffortButtonProps) {
  const [open, setOpen] = useState(false)
  if (levels.length === 0) return null

  const pick = (level: string | null) => {
    onChange(level)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="推理强度"
        aria-expanded={open}
        title={`推理强度 ${effort ?? '不设'}`}
        onClick={() => setOpen((value) => !value)}
        className={cx(
          'inline-flex h-[26px] items-center gap-a6 rounded-sm px-a8 font-ui text-caption transition-colors duration-fast ease-out hover:bg-overlay-light focus-visible:bg-overlay-light',
          effort === null ? 'text-ink-light' : 'text-ink',
        )}
      >
        <Icon name="activity" size={12} />
        {/* nowrap: the chip is meant to be as wide as its level name, never squeezed into two lines. */}
        <span className="whitespace-nowrap">{effort ?? '不设'}</span>
        <span
          className={cx('text-ink-muted transition-transform duration-fast ease-out', open ? '' : 'rotate-180')}
        >
          <Icon name="chevron-down" size={12} />
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="推理强度"
          className="absolute bottom-full right-0 z-20 mb-a8 w-[180px] rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
        >
          {[null, ...levels].map((level) => (
            <button
              key={level ?? 'unset'}
              type="button"
              onClick={() => pick(level)}
              className={cx(
                'flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a4 text-left font-ui text-caption transition-colors duration-fast ease-out',
                effort === level ? 'bg-accent-light text-accent' : 'text-ink hover:bg-overlay-light',
              )}
            >
              <span>{level ?? '不设'}</span>
              {effort === level && (
                <span className="shrink-0 text-accent">
                  <Icon name="check" size={12} />
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
