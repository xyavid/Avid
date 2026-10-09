/**
 * Collapsible thinking segment fed by the streamed `reasoning_delta`; expanded while
 * streaming, folded to one line when the run ends. It exists only in the stream: deltas are
 * a delta event type (`DELTA_EVENT_TYPES` in `agent/events.py`) and never enter the session
 * JSONL, so a reload drops it by design.
 */

import { useEffect, useState } from 'react'

import { durationLabel } from '../../ui/duration'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ReasoningBlockProps = {
  /** Accumulated reasoning text; empty string renders nothing. */
  text: string
  /** Run in flight: expanded while streaming, folded when done. */
  streaming?: boolean
  /** First-to-last delta span; null = no reading, so the row says "done" only. */
  durationMs?: number | null
}

export function ReasoningBlock({ text, streaming = false, durationMs = null }: ReasoningBlockProps) {
  const [open, setOpen] = useState(streaming)

  // Fold when streaming ends; an effect, so a manual re-open is not undone.
  useEffect(() => {
    if (!streaming) setOpen(false)
  }, [streaming])

  if (text === '') return null

  const label = streaming
    ? '思考中…'
    : durationMs === null
      ? '思考完成'
      : `思考 · ${durationLabel(durationMs)}`

  return (
    <div className="my-a8 overflow-hidden rounded-sm border-hairline border-hair bg-overlay-subtle">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-a6 px-a8 py-a4 text-left transition-colors duration-fast ease-out hover:bg-overlay-light"
      >
        <span className={cx('shrink-0 text-ink-muted transition-transform duration-fast ease-out', open ? '' : '-rotate-90')}>
          <Icon name="chevron-down" size={12} />
        </span>
        <span className="font-ui text-hint text-ink-muted">{label}</span>
        {streaming && (
          <span
            aria-hidden
            className="h-[4px] w-[4px] rounded-full bg-accent"
            style={{ animation: 'hana-cycling-dots 1.2s var(--ease-standard) infinite' }}
          />
        )}
      </button>
      {open && (
        <div className="whitespace-pre-wrap border-t border-hair px-a12 py-a8 font-ui text-ui leading-[1.7] text-ink-light">
          {text}
        </div>
      )}
    </div>
  )
}
