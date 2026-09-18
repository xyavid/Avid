import { clsx } from 'clsx'
import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'icon'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

// 高度只用 --sticker 档位表达；按压位移 = 本档偏移（.press-2，档位随之缩放）。
//
// 变体分工（2026-09 实测修正）：**行动型按键一律有方框**——primary / secondary /
// danger 都走 `.sketch-chip`（墨线边 + --sketch-r-chip 圆角 + 硬阴影），悬停时由
// sketch.css 抬升一档。`ghost` 只留给「标题/链接型」控件（会话列表里的会话标题），
// 它确实不该有边框：整块卡片点进去，加框反而像按钮套按钮。
// 时间线的「复制文本 / 查看原始 JSON」属于行动型，用 secondary：方框是**本身就有**的，
// 悬停只负责让这一行动作显形（opacity 由 EntryRow 控制），不是方框的来源。
const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-ink',
  secondary: 'bg-card text-ink',
  ghost: 'bg-transparent border-transparent shadow-none',
  danger: 'bg-danger-bg text-ink',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'min-h-control px-3 text-sm',
  md: 'min-h-control px-4 text-base',
  icon: 'h-control w-control p-0',
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  className,
  disabled,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        'press press-2 inline-flex shrink-0 items-center justify-center gap-2 rounded-chip border-hair border-ink font-sketch leading-none',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        variant === 'ghost' ? 'border-transparent' : 'sketch-chip',
        className,
      )}
    >
      {children}
    </button>
  )
}
