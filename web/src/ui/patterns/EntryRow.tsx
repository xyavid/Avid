/**
 * 时间线条目：用户是右侧便签，assistant 走 Markdown，工具结果与 notice 是 chip。
 *
 * `aria-live` 只加在 durable（非乐观）的 assistant 条目上：乐观 delta 每帧都在变，读屏器
 * 会把它念成一串噪音，而 durable 消息才是完整的一句话。动作行默认透明，鼠标悬停或键盘
 * 聚焦时显形——`focus-visible` 直接写在按钮上，这样键盘用户 tab 到哪个按钮哪个就可见
 * （透明放在容器上会把子元素一起吃掉，键盘用户将永远看不见动作）。
 */
import { clsx } from 'clsx'

import { useTranslation } from '../../lib/i18n'
import { Markdown } from '../../lib/markdown'
import type { TimelineEntry } from '../../events/reducer'
import type { Density } from '../../state/uiStore'
import { Badge, Button } from '../../ui/primitives'

export interface EntryRowProps {
  entry: TimelineEntry
  density?: Density
  onInspect?: (entry: TimelineEntry) => void
  onCopy?: (text: string) => void
}

const NOTICE_KEY = {
  compaction: 'chat.notice.compaction',
  todo: 'chat.notice.todo',
  nudge: 'chat.notice.nudge',
} as const

const ACTION = 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'

function Actions({ entry, onInspect, onCopy }: Pick<EntryRowProps, 'entry' | 'onInspect' | 'onCopy'>) {
  const { t } = useTranslation()
  if (!onCopy && !onInspect) return null
  return (
    <div className="mt-1 flex gap-1">
      {onCopy ? (
        <Button size="sm" variant="secondary" className={ACTION} onClick={() => onCopy(entry.text)}>
          {t('chat.message.copy')}
        </Button>
      ) : null}
      {onInspect ? (
        <Button size="sm" variant="secondary" className={ACTION} onClick={() => onInspect(entry)}>
          {t('chat.message.raw')}
        </Button>
      ) : null}
    </div>
  )
}

export function EntryRow({ entry, density = 'comfy', onInspect, onCopy }: EntryRowProps) {
  const { t } = useTranslation()
  const pad = density === 'compact' ? 'p-2' : 'p-3'
  const actions = <Actions entry={entry} onInspect={onInspect} onCopy={onCopy} />

  if (entry.kind === 'user') {
    return (
      <article className={clsx('group sketch-card ml-auto w-fit max-w-[80%]', pad)}>
        <p className="font-sketch text-xs text-ink/70">{t('chat.message.role.user')}</p>
        <p className="whitespace-pre-wrap break-anywhere text-sm">{entry.text}</p>
        {actions}
      </article>
    )
  }
  if (entry.kind === 'notice') {
    const label = entry.notice ? t(NOTICE_KEY[entry.notice]) : ''
    return (
      <article className="group sketch-chip flex w-fit max-w-[32rem] items-center gap-2 px-3 py-1">
        <span className="shrink-0 font-sketch text-xs">{label}</span>
        <span className="min-w-0 truncate text-xs text-ink/70">{entry.text}</span>
        {actions}
      </article>
    )
  }
  if (entry.kind === 'tool') {
    return (
      <article className="group flex flex-col gap-1">
        <Badge tone="neutral">{t('chat.message.role.tool')}</Badge>
        <pre className="term scroll-area max-h-64 overflow-x-auto whitespace-pre-wrap rounded-sketch-2 p-2 shadow-sticker-2">
          {entry.text}
        </pre>
        {actions}
      </article>
    )
  }
  return (
    <article className="group" aria-live={entry.optimistic ? undefined : 'polite'}>
      <Markdown text={entry.text} className={pad} />
      {actions}
    </article>
  )
}
