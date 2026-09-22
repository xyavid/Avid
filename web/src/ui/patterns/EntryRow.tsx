/**
 * 时间线条目：用户是右侧对话框，模型回复是左侧对话框，工具结果与 notice 是 chip。
 *
 * 模型回复与用户消息是**同一族卡片**（`surface-card`：玻璃面 + 高光边 + 柔和投影），
 * 差别只在方向与角色标记。旧语言里的「形状轮换」（相邻卡片取三种手绘圆角之一）随
 * 涂鸦机制一起删除：新语言的卡片是同一形状，区分靠方向与角色标记，不靠形变。
 *
 * 没有正文的模型回复**不归这里管**：只声明工具调用、正文为空的那一轮由
 * `conversation/lib/groupTimeline` 直接跳过（它的工具调用本来就有工具卡），所以这个
 * 组件拿到的 assistant 条目一定有正文。注入的提醒（TODO / nudge）同理不进时间线。
 *
 * 角色标记用两枚 lucide 图标（`Bot` / `User`），细笔画（1.75）；旧的两位手绘标记随
 * 涂鸦资产一起删除。标记是装饰，不进无障碍树，角色名由旁边文字承担。
 *
 * `aria-live` 只加在 durable（非乐观）的 assistant 条目上：乐观 delta 每帧都在变，读屏器
 * 会把它念成一串噪音，而 durable 消息才是完整的一句话。
 *
 * 动作行**常驻可见**（复制文本 / 从此处分支）：以前是 `opacity-0` + 悬停显形，代价是
 * 「有这功能」本身要先被猜到；方框与高度档本来就一直在，显隐只是额外的一层谜。
 * 条目级的「查看原始 JSON」按钮已删除——检查器改由工具卡的「查看」打开（那条路径仍在，
 * 工具输出才是真正需要看全文/diff 的东西）。
 */
import { memo } from 'react'

import { clsx } from 'clsx'

import { useTranslation } from '../../lib/i18n'
import { Markdown } from '../../lib/markdown'
import type { Density } from '../../lib/density'
import type { TimelineEntry } from '../../lib/timeline'
import { Bot, User } from 'lucide-react'

import { Badge, Button } from '../../ui/primitives'

export interface EntryRowProps {
  entry: TimelineEntry
  density?: Density
  onCopy?: (text: string) => void
  /** 「从此处分支」：只有落了库的条目（有 `entryId`）才可能成为分叉点。 */
  onFork?: (entry: TimelineEntry) => void
}

const NOTICE_KEY = { compaction: 'chat.notice.compaction' } as const

/**
 * 角色标记：两枚 lucide 图标（模型 `Bot` / 用户 `User`），`aria-hidden` 由 lucide
 * 默认带上——标记是装饰，作者由旁边的角色名说明。
 */
function Role({ label, mark = 'avid' }: { label: string; mark?: 'avid' | 'user' }) {
  const Icon = mark === 'user' ? User : Bot
  return (
    <p className="flex items-center gap-1 text-xs text-ink-muted">
      <Icon size={16} strokeWidth={1.75} className="shrink-0 text-ink" aria-hidden="true" />
      {label}
    </p>
  )
}

function Actions({
  entry,
  onCopy,
  onFork,
}: Pick<EntryRowProps, 'entry' | 'onCopy' | 'onFork'>) {
  const { t } = useTranslation()
  if (!onCopy && !onFork) return null
  // 分叉点是条目树里的一个 id：乐观条目（delta）还没有 id，不能当分叉点。
  const forkable = Boolean(onFork && entry.entryId)
  return (
    <div className="mt-1 flex gap-1">
      {onCopy ? (
        <Button size="sm" variant="secondary" onClick={() => onCopy(entry.text)}>
          {t('chat.message.copy')}
        </Button>
      ) : null}
      {forkable ? (
        <Button size="sm" variant="secondary" onClick={() => onFork?.(entry)}>
          {t('chat.message.fork')}
        </Button>
      ) : null}
    </div>
  )
}

export const EntryRow = memo(function EntryRow({
  entry,
  density = 'comfy',
  onCopy,
  onFork,
}: EntryRowProps) {
  const { t } = useTranslation()
  const pad = density === 'compact' ? 'p-2' : 'p-3'
  const actions = <Actions entry={entry} onCopy={onCopy} onFork={onFork} />

  if (entry.kind === 'user') {
    return (
      <article
        className={clsx('surface-card ml-auto w-fit max-w-[80%]', pad)}
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
      <article className="surface-chip flex w-fit max-w-[32rem] items-center gap-2 px-3 py-1">
        <span className="shrink-0 text-xs">{label}</span>
        <span className="min-w-0 truncate text-xs text-ink-muted">{entry.text}</span>
        {actions}
      </article>
    )
  }
  if (entry.kind === 'tool') {
    return (
      <article className="flex flex-col gap-1">
        <Badge tone="neutral">{t('chat.message.role.tool')}</Badge>
        <pre className="term scroll-area max-h-64 overflow-x-auto whitespace-pre-wrap rounded-card p-2 shadow-lift-2">
          {entry.text}
        </pre>
        {actions}
      </article>
    )
  }

  // 正文为空的 assistant 回合不会走到这里（groupTimeline 已跳过它，工具调用由工具卡承担）。
  return (
    <article
      aria-live={entry.optimistic ? undefined : 'polite'}
      className={clsx('surface-card mr-auto flex w-fit max-w-[88%] flex-col gap-1', pad)}
    >
      <Role label={t('chat.message.role.assistant')} />
      <Markdown text={entry.text} />
      {actions}
    </article>
  )
})
