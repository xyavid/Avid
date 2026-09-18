/**
 * 时间线条目：用户是右侧便签，模型回复是左侧对话框，工具结果与 notice 是 chip。
 *
 * 模型回复与用户消息是**同一族卡片**（`sketch-card`：墨框 + 手绘形状 + `--sticker-4`
 * 硬阴影），差别只在方向与角色标记——参照实现 purrcat 的对话框就是这一套
 * （`bg-paper` + `border-4 border-ink` + 大硬阴影 + 小角度倾斜），这里把它的外壳
 * 语言用在消息上，尺寸按消息收敛。形状按 `shapeIndex` 轮换，相邻卡片不同形。
 *
 * 只有确实有正文的模型回复才用整张卡；只声明工具调用、正文为空的那一轮退化成
 * 一枚 chip（图标 + 角色名），免得每轮工具调用都多出一个空框。
 *
 * 角色标记按作者分两枚：模型是 `AvidMark`（角形笔画），用户是 `UserMark`（歪头 +
 * 肩弧）。两者笔触语言一致（`stroke-width 4.5` + `currentColor` + −2deg 倾斜），
 * 但形状不同——标记的职责就是区分作者。
 *
 * `aria-live` 只加在 durable（非乐观）的 assistant 条目上：乐观 delta 每帧都在变，读屏器
 * 会把它念成一串噪音，而 durable 消息才是完整的一句话。动作行默认透明，鼠标悬停或键盘
 * 聚焦时显形——`focus-visible` 直接写在按钮上，这样键盘用户 tab 到哪个按钮哪个就可见
 * （透明放在容器上会把子元素一起吃掉，键盘用户将永远看不见动作）。
 */
import { memo } from 'react'

import { clsx } from 'clsx'

import { useTranslation } from '../../lib/i18n'
import { Markdown } from '../../lib/markdown'
import type { Density } from '../../lib/density'
import type { TimelineEntry } from '../../lib/timeline'
import { Badge, Button } from '../../ui/primitives'
import { AvidMark, UserMark, shapeFor } from '../../ui/sketch'

export interface EntryRowProps {
  entry: TimelineEntry
  /** 同级卡片的轮换序号：按**全部**条目计算，所以加载更早不会改变已有卡片的形状。 */
  shapeIndex?: number
  density?: Density
  onInspect?: (entry: TimelineEntry) => void
  onCopy?: (text: string) => void
  /** 「从此处分支」：只有落了库的条目（有 `entryId`）才可能成为分叉点。 */
  onFork?: (entry: TimelineEntry) => void
}

const NOTICE_KEY = {
  compaction: 'chat.notice.compaction',
  todo: 'chat.notice.todo',
  nudge: 'chat.notice.nudge',
} as const

const ACTION = 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'

/**
 * 角色标记：两种角色各一枚专属手绘小标记（`avid` 是角形笔画，`user` 是歪头 + 肩弧）。
 * 不复用同一枚：标记的作用就是标明作者，模型卡与用户卡共用一个形状等于没标。
 */
function Role({ label, mark = 'avid' }: { label: string; mark?: 'avid' | 'user' }) {
  return (
    <p className="flex items-center gap-1 font-sketch text-xs text-ink/70">
      {mark === 'user' ? <UserMark className="text-ink" /> : <AvidMark className="text-ink" />}
      {label}
    </p>
  )
}

function Actions({
  entry,
  onInspect,
  onCopy,
  onFork,
}: Pick<EntryRowProps, 'entry' | 'onInspect' | 'onCopy' | 'onFork'>) {
  const { t } = useTranslation()
  if (!onCopy && !onInspect && !onFork) return null
  // 分叉点是条目树里的一个 id：乐观条目（delta）还没有 id，不能当分叉点。
  const forkable = Boolean(onFork && entry.entryId)
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
      {forkable ? (
        <Button size="sm" variant="secondary" className={ACTION} onClick={() => onFork?.(entry)}>
          {t('chat.message.fork')}
        </Button>
      ) : null}
    </div>
  )
}

export const EntryRow = memo(function EntryRow({
  entry,
  shapeIndex = 0,
  density = 'comfy',
  onInspect,
  onCopy,
  onFork,
}: EntryRowProps) {
  const { t } = useTranslation()
  const pad = density === 'compact' ? 'p-2' : 'p-3'
  const actions = <Actions entry={entry} onInspect={onInspect} onCopy={onCopy} onFork={onFork} />

  if (entry.kind === 'user') {
    return (
      <article
        className={clsx('group sketch-card ml-auto w-fit max-w-[80%]', shapeFor(shapeIndex), pad)}
      >
        <Role label={t('chat.message.role.user')} mark="user" />
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

  // 只是一个工具调用的「空回合」：用 chip 标记，别为它撑起一张空卡
  if (!entry.text.trim()) {
    return (
      <article className="group sketch-chip flex w-fit items-center gap-2 px-3 py-1">
        <Role label={t('chat.message.role.assistant')} />
        {actions}
      </article>
    )
  }

  return (
    <article
      aria-live={entry.optimistic ? undefined : 'polite'}
      className={clsx(
        'group sketch-card mr-auto flex w-fit max-w-[88%] flex-col gap-1',
        shapeFor(shapeIndex),
        pad,
      )}
    >
      <Role label={t('chat.message.role.assistant')} />
      <Markdown text={entry.text} />
      {actions}
    </article>
  )
})
