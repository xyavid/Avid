/**
 * Model chip in the composer: picks this run's model from the BYOK candidates (the kernel ships
 * no presets); reasoning effort is its own chip — see `EffortButton`.
 */

import { useState } from 'react'

import type { ModelCandidate } from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ModelButtonProps = {
  /** Model for this run (providerId/modelId); null = not chosen yet. */
  model: string | null
  onChange: (model: string) => void
  /** BYOK candidates (providerId/modelId ref + label) from the settings page. */
  candidates: ModelCandidate[]
}

export function ModelButton({ model, onChange, candidates }: ModelButtonProps) {
  const [open, setOpen] = useState(false)
  const pick = (next: string) => {
    onChange(next)
    setOpen(false)
  }
  const current = candidates.find((candidate) => candidate.ref === model)
  const label = current?.label ?? model ?? '选择模型'

  return (
    // min-w-0 down the chain (wrapper, chip): without it the chip's min-content — the untruncated
    // label — blocks the flex shrink, and the row pushes the overflow onto the action button.
    <div className="relative flex min-w-0">
      <button
        type="button"
        aria-label="模型"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cx(
          'inline-flex h-[26px] min-w-0 items-center gap-a6 rounded-sm px-a8 font-ui text-caption transition-colors duration-fast ease-out',
          // Not chosen yet: accent text plus accent hover, since the disabled send button's reason
          // must show without a resting border.
          model === null ? 'text-accent hover:bg-accent-light' : 'text-ink hover:bg-overlay-light',
        )}
      >
        <Icon name="sparkle" size={12} />
        <span className="max-w-[180px] truncate">{label}</span>
        <span className={cx('text-ink-muted transition-transform duration-fast ease-out', open ? '' : 'rotate-180')}>
          <Icon name="chevron-down" size={12} />
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="模型"
          className="absolute bottom-full left-1/2 z-20 mb-a8 w-[300px] -translate-x-1/2 rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
        >
          {candidates.length > 0 ? (
            candidates.map((candidate) => (
              <button
                key={candidate.ref}
                type="button"
                onClick={() => pick(candidate.ref)}
                className="flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a4 text-left font-ui text-caption text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="min-w-0 truncate">{candidate.label}</span>
                  <span className="min-w-0 truncate font-ui text-micro text-ink-muted">{candidate.ref}</span>
                </span>
                {model === candidate.ref && (
                  <span className="shrink-0 text-accent">
                    <Icon name="check" size={12} />
                  </span>
                )}
              </button>
            ))
          ) : (
            <p className="px-a8 py-a4 font-ui text-micro text-ink-muted">
              还没有自定义模型——在「设置 → 模型」里添加 BYOK 提供商后，这里就会出现候选。
            </p>
          )}
        </div>
      )}
    </div>
  )
}
