/**
 * 思考块：把模型流出来的 reasoning（`reasoning_delta`）显示成时间线上的一段。
 *
 * 为什么是「折叠块」而不是直接铺在正文前面：思考是过程不是结论——流式时它值得看
 * （能判断模型有没有跑偏），收尾后它只会挤占正文的位置。所以流式时展开、收尾自动
 * 折成一行，想回看再点开；折叠行带持续时长（首末增量的时间差），"想了多久"是
 * 判断它跑不跑偏的一半信息。
 *
 * **它只在流里存在**：`reasoning_delta` 属于 delta 档（`agent/events.py` 的
 * `DELTA_EVENT_TYPES`），不进会话 JSONL、不参与重放——刷新或切走会话后就没有了。
 * 这是刻意的：思考内容不进 durable（既省盘，也避免把它当事实回灌给模型）。
 */

import { useEffect, useState } from 'react'

import { durationLabel } from '../../ui/duration'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ReasoningBlockProps = {
  /** 累积的思考文本；空串 = 这段没有内容，整块不渲染。 */
  text: string
  /** 运行是否还在进行：流式时展开、收尾自动折起。 */
  streaming?: boolean
  /** 这段思考的持续时长（首末增量的时间差）；null = 没有读数，折叠行只说「完成」。 */
  durationMs?: number | null
}

export function ReasoningBlock({ text, streaming = false, durationMs = null }: ReasoningBlockProps) {
  const [open, setOpen] = useState(streaming)

  // 收尾那一刻折起来。用 effect 而不是派生值：用户可能在流式时手动折过，
  // 收尾后又会手动点开——那之后不该再被它拨回去。
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
