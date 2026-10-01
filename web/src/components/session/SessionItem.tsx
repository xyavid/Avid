/**
 * 会话列表项（组件墙 §会话列表项 + 报告 §7.1）：
 * - 常态透明，hover/active 转 accent 浅底；
 * - active 标题转 accent（字重 500——墙用 600，违反报告 §5「字重仅 400/500」，
 *   这里以纪律优先，色相是主信号）；
 * - 操作按钮「隐藏直到需要」：opacity-0，group-hover / 键盘 focus-within 淡入
 *   （duration-fast），用透明度而非墙单行模式的宽度展开，避免布局抖动；
 * - 流式会话带 5px accent 呼吸圆点（报告 §7.1，hana-pulse）。
 */

import { cx } from '../../ui/cx'
import { IconButton } from '../../ui/IconButton'

export type SessionItemProps = {
  title: string
  meta: string
  active?: boolean
  streaming?: boolean
  className?: string
}

export function SessionItem({ title, meta, active = false, streaming = false, className }: SessionItemProps) {
  return (
    <div
      className={cx(
        'group rounded-sm px-[9px] py-[7px] transition-colors duration-fast ease-out',
        active ? 'bg-accent-light' : 'hover:bg-accent-light',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-a8">
        <span className={cx('truncate font-ui text-ui', active ? 'font-medium text-accent' : 'text-ink')}>
          {title}
        </span>
        <span
          data-testid="session-actions"
          className="flex shrink-0 items-center gap-[5px] opacity-0 transition-opacity duration-fast ease-out group-hover:opacity-100 group-focus-within:opacity-100"
        >
          <IconButton icon="pin" label="置顶" />
          <IconButton icon="archive" label="归档" />
        </span>
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
    </div>
  )
}
