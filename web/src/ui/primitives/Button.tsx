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
        'press press-2 inline-flex shrink-0 items-center justify-center gap-2 border-hair border-ink font-sketch leading-none',
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
