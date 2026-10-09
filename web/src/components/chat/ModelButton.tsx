/**
 * Model chip in the composer: picks this run's model and reasoning effort from the BYOK
 * candidates (the kernel ships no presets). Effort levels come from the selected model's own
 * declaration — the list plus an unset option that keeps the parameter out of the request.
 */

import { useState } from 'react'

import type { ModelCandidate } from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ModelButtonProps = {
  /** Model for this run (providerId/modelId); null = not chosen yet. */
  model: string | null
  onChange: (model: string) => void
  /** Reasoning effort for this run; null = unset, so the request omits the parameter. */
  effort?: string | null
  onChangeEffort?: (effort: string | null) => void
  /** BYOK candidates (providerId/modelId ref + label) from the settings page. */
  candidates: ModelCandidate[]
}

export function ModelButton({
  model,
  onChange,
  effort = null,
  onChangeEffort,
  candidates,
}: ModelButtonProps) {
  const [open, setOpen] = useState(false)
  const pick = (next: string) => {
    onChange(next)
    setOpen(false)
  }
  const current = candidates.find((candidate) => candidate.ref === model)
  // Effort levels follow the candidate; empty means the model declares none, so hide the section.
  const efforts = current?.reasoning_efforts ?? []
  const label = current?.label ?? model ?? '选择模型'
  const pickEffort = (level: string | null) => {
    onChangeEffort?.(level)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="模型"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cx(
          'inline-flex h-[26px] items-center gap-a6 rounded-sm border-hairline px-a8 font-ui text-caption transition-colors duration-fast ease-out hover:bg-overlay-light',
          // Not chosen yet: accent outline, since the disabled send button's reason must show.
          model === null ? 'border-accent text-accent' : 'border-hair text-ink',
        )}
      >
        <Icon name="sparkle" size={12} />
        <span className="max-w-[180px] truncate">{label}</span>
        {effort !== null && <span className="shrink-0 text-ink-muted">{`· ${effort}`}</span>}
        <span className={cx('text-ink-muted transition-transform duration-fast ease-out', open ? '' : 'rotate-180')}>
          <Icon name="chevron-down" size={12} />
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="模型"
          className="absolute bottom-full left-0 z-20 mb-a8 w-[300px] rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
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

          {efforts.length > 0 && onChangeEffort !== undefined && (
            <>
              <p className="mt-a6 border-t border-hair pt-a6 px-a8 font-ui text-micro text-ink-muted">
                推理强度
              </p>
              {[null, ...efforts].map((level) => (
                <button
                  key={level ?? 'unset'}
                  type="button"
                  onClick={() => pickEffort(level)}
                  className="flex w-full items-center justify-between gap-a8 rounded-sm px-a8 py-a4 text-left font-ui text-caption text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
                >
                  <span className="min-w-0 truncate">{level ?? '不设'}</span>
                  {effort === level && (
                    <span className="shrink-0 text-accent">
                      <Icon name="check" size={12} />
                    </span>
                  )}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
