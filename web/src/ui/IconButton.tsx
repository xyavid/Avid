/**
 * 图标按钮（报告 §7.6）：26px、圆角 sm。
 * ghost（默认）：hover 用 overlay-light 轻压痕；primary（阶段 4 补件）：
 * accent 填充（composer 发送钮）——hover 落 accent-hover。
 * 必须给 label——图标按钮没有文字，这是无障碍的硬要求（aria-label + title）。
 */

import type { ButtonHTMLAttributes } from 'react'

import { cx } from './cx'
import { Icon, type IconName } from './Icon'

export type IconButtonVariant = 'ghost' | 'primary'

export type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & {
  icon: IconName
  label: string
  iconSize?: number
  variant?: IconButtonVariant
}

const BASE =
  'inline-flex h-[26px] w-[26px] items-center justify-center rounded-sm transition-colors duration-fast ease-out disabled:cursor-not-allowed disabled:opacity-40'

const VARIANTS: Record<IconButtonVariant, string> = {
  ghost: 'text-ink-muted hover:bg-overlay-light hover:text-ink',
  primary: 'bg-accent text-card hover:bg-accent-hover',
}

export function IconButton({ icon, label, iconSize = 14, variant = 'ghost', className, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cx(BASE, VARIANTS[variant], className)}
      {...rest}
    >
      <Icon name={icon} size={iconSize} />
    </button>
  )
}
