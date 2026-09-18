import { clsx } from 'clsx'
import type { HTMLAttributes, ReactNode } from 'react'

export type BadgeTone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'mark'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  /** 有计数时显示在右侧；0 不显示。 */
  count?: number
  pulse?: boolean
  children?: ReactNode
}

// 状态色永远「深色文字 + 浅色底」；-bg 系列只做填充（§8.2 的对比度红线）。
const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-sand text-ink',
  ok: 'bg-ok-bg/40 text-ok',
  warn: 'bg-warn-bg/40 text-warn',
  danger: 'bg-danger-bg/40 text-danger',
  info: 'bg-info-bg/40 text-info',
  mark: 'bg-mark/50 text-ink',
}

export function Badge({
  tone = 'neutral',
  count,
  pulse = false,
  className,
  children,
  ...rest
}: BadgeProps) {
  return (
    <span
      {...rest}
      className={clsx(
        'inline-flex items-center gap-1 rounded-chip border-hair border-ink px-2 py-0.5 text-xs font-sketch',
        TONES[tone],
        pulse && 'pulse',
        className,
      )}
    >
      {children}
      {count !== undefined && count > 0 ? <span className="font-mono">{count}</span> : null}
    </span>
  )
}
