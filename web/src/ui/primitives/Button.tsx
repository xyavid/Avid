import { clsx } from 'clsx'
import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'icon'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

// 高度只用 --sticker/--control 档位表达；按压位移 = 本档偏移（.press-2）。
//
// ghost 是「安静按钮」：静止时没有方框（透明边 + 无底色 + 无阴影），但**形状、
// 高度与交互反馈与其它按钮同一套**——悬停/键盘聚焦时由 .quiet-chip 给出墨线方框
// 与 --sticker-1 档阴影，按下时位移量等于该档偏移。这样时间线里的「复制文本 /
// 查看原始 JSON」在保持安静的同时，与旁边的次级按钮看起来是同一族控件。
const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-ink',
  secondary: 'bg-card text-ink',
  ghost: 'quiet-chip border-transparent bg-transparent shadow-none',
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
