/**
 * 会话列表项（组件墙 §会话列表项 + 报告 §7.1）：
 * - 常态透明，hover/active 转 accent 浅底；
 * - active 标题转 accent（字重 500——墙用 600，违反报告 §5「字重仅 400/500」，
 *   这里以纪律优先，色相是主信号）；
 * - 操作按钮「隐藏直到需要」：opacity-0，group-hover / 键盘 focus-within 淡入
 *   （duration-fast），用透明度而非墙单行模式的宽度展开，避免布局抖动；
 * - 两个动作都是真的（阶段 13）：重命名 = 行内编辑（Enter 提交 / Esc 取消），
 *   删除 = 行下确认条（销毁磁盘记录，不可恢复）。**不给回调就一个都不渲染**——
 *   组件墙的静态演示因此不会出现按不动的按钮；原先的置顶/归档是假动作（后端
 *   没有这个概念，也不造假分组），随本阶段下线；
 * - 流式会话带 5px accent 呼吸圆点（报告 §7.1，hana-pulse）。
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
  /** 提供即整项可点（role=button + 键盘可达），focus-within 会点亮操作按钮 */
  onSelect?: () => void
  /** 提供即出现「重命名」；提交的是 trim 过、非空且确有变化的名字。 */
  onRename?: (name: string) => void
  /** 提供即出现「删除」；按一下先出确认条，确认才回调（删除不可恢复）。 */
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
  /** 编辑草稿：null = 不在编辑；每次进编辑都以当前标题重新起稿（不继承上次的改动）。 */
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
        // 行内的输入框与按钮自己处理键盘；这里只管「整项可点」那条路径。
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
