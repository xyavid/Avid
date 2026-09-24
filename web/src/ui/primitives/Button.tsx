import { clsx } from 'clsx'
import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'icon'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

// 三个变体都走 `.surface-chip`（玻璃面 + 高光边 + --lift-1 档投影）；悬停抬一档投影，
// 按住收掉投影。高度层级由投影承担，所以这里不再需要「按压位移 = 阴影偏移」那套约定。
//
// primary 用**压深过**的强调色配近白字（`--avid-accent-deep-rgb` + `text-card`）：
// 原来的 `bg-accent text-ink` 在玻璃底上只有 4.19:1，不达 AA（阶段 23b 实测）。
//
// 曾经有过一个 `ghost`（无框）变体，只给会话列表的标题用；实测下来标题同样该有框
// （和它下面的「改名 / 删除」是同一排控件，没框时不像一族），于是变体删掉、调用点改用
// secondary。删掉而不是留着备用：没有调用点的变体是不可验证的死代码。
const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent-deep text-card',
  secondary: 'text-ink',
  danger: 'bg-danger-bg text-ink',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'min-h-control px-3 text-sm',
  md: 'min-h-control px-4 text-base',
  icon: 'h-control w-control p-0',
}

/**
 * 按钮转发 ref：`Tooltip`（Radix）用 `asChild` 把触发元素的 ref 交给子组件当浮层锚点，
 * 不转发的话气泡拿不到锚点、位置算不出来（`Input` 早就因为同类原因转发过，见 `Field.tsx`）。
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    className,
    disabled,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        'press inline-flex shrink-0 items-center justify-center gap-2 rounded-chip leading-none',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        'surface-chip',
        className,
      )}
    >
      {children}
    </button>
  )
})
