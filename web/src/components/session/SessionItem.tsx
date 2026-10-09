/**
 * Session list item: rename is inline editing (Enter commits, Esc cancels) and delete opens a
 * confirmation row, because it destroys the session file on disk and cannot be undone; neither
 * action renders unless its callback is provided.
 * Action buttons stay at opacity-0 until hover or keyboard focus, and a streaming session shows a
 * 5px `hana-pulse` accent dot.
 */

import { useState } from 'react'

import { cx } from '../../ui/cx'
import { IconButton } from '../../ui/IconButton'
import { Input } from '../../ui/Input'

export type SessionItemProps = {
  title: string
  meta: string
  active?: boolean
  streaming?: boolean
  /** Providing it makes the whole row clickable (role=button, keyboard reachable). */
  onSelect?: () => void
  /** Providing it shows rename; commits a trimmed, non-empty, actually changed name. */
  onRename?: (name: string) => void
  /** Providing it shows delete; the confirmation row is accepted first (deletion is final). */
  onDelete?: () => void
  className?: string
}

export function SessionItem({
  title,
  meta,
  active = false,
  streaming = false,
  onSelect,
  onRename,
  onDelete,
  className,
}: SessionItemProps) {
  /** Edit draft: null = not editing; each edit starts from the title, not the previous draft. */
  const [draft, setDraft] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  const commit = () => {
    const next = (draft ?? '').trim()
    setDraft(null)
    if (next && next !== title) onRename?.(next)
  }

  return (
    <div
      role={onSelect ? 'button' : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onClick={onSelect}
      onKeyDown={(e) => {
        // The inline input and buttons handle their own keys; this is the row-activation path.
        if (!onSelect || e.target !== e.currentTarget) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
      className={cx(
        'group rounded-sm px-[9px] py-[7px] transition-colors duration-fast ease-out',
        active ? 'bg-accent-light' : 'hover:bg-accent-light',
        onSelect && 'cursor-pointer',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-a8">
        {draft === null ? (
          <span className={cx('truncate font-ui text-ui', active ? 'font-medium text-accent' : 'text-ink')}>
            {title}
          </span>
        ) : (
          <Input
            bare
            autoFocus
            aria-label="会话名称"
            value={draft}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit()
              else if (e.key === 'Escape') setDraft(null)
            }}
            className="h-[20px] rounded-xs bg-overlay-light px-a4"
          />
        )}
        {(onRename || onDelete) && draft === null && (
          <span
            data-testid="session-actions"
            className="flex shrink-0 items-center gap-[5px] opacity-0 transition-opacity duration-fast ease-out group-hover:opacity-100 group-focus-within:opacity-100"
          >
            {onRename && (
              <IconButton
                icon="pencil"
                label={`重命名：${title}`}
                onClick={(e) => {
                  e.stopPropagation()
                  setDraft(title)
                }}
              />
            )}
            {onDelete && (
              <IconButton
                icon="trash-2"
                label={`删除：${title}`}
                aria-expanded={confirming}
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirming((v) => !v)
                }}
              />
            )}
          </span>
        )}
      </div>
      <div className="mt-[1px] flex items-center gap-[5px] font-ui text-hint text-ink-muted opacity-80">
        {streaming && (
          <span
            data-testid="streaming-dot"
            aria-hidden
            className="h-[5px] w-[5px] shrink-0 rounded-full bg-accent"
            style={{ animation: 'hana-pulse 1.6s ease-in-out infinite' }}
          />
        )}
        <span className="truncate">{meta}</span>
      </div>
      {confirming && onDelete && (
        <div className="mt-a4 flex items-center justify-between gap-a8 rounded-sm border-hairline border-hair bg-card px-a8 py-a4">
          <span className="font-ui text-micro text-ink-muted">删除会话会销毁磁盘上的记录文件，不可恢复</span>
          <span className="flex shrink-0 items-center gap-a4">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                setConfirming(false)
              }}
              className="rounded-xs px-a8 py-[1px] font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
            >
              取消
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                setConfirming(false)
                onDelete()
              }}
              className="rounded-xs px-a8 py-[1px] font-ui text-micro text-danger transition-colors duration-fast ease-out hover:bg-overlay-light"
            >
              确认删除
            </button>
          </span>
        </div>
      )}
    </div>
  )
}
