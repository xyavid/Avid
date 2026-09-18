import { clsx } from 'clsx'
import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'icon'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

// 高度只用 --sticker 档位表达；按压位移 = 本档偏移（.press-2，档位随之缩放）。
//
// 变体只有三种，**每一个都自带方框**：primary / secondary / danger 都走 `.sketch-chip`
// （墨线边 + --sketch-r-chip 圆角 + --sticker-2 档硬阴影），悬停时由 sketch.css 抬升一档，
// 按住时阴影归零、位移等于当前档偏移。
//
// 曾经有过一个 `ghost`（无框）变体，只给会话列表的标题用；实测下来标题同样该有框
// （和它下面的「改名 / 删除」是同一排控件，没框时不像一族），于是变体删掉、调用点改用
// secondary。删掉而不是留着备用：没有调用点的变体是不可验证的死代码。
const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-ink',
  secondary: 'bg-card text-ink',
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
        'sketch-chip',
        className,
      )}
    >
      {children}
    </button>
  )
}
