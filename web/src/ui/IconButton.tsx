/**
 * 图标按钮（报告 §7.6）：26px、圆角 sm、hover 用 overlay-light 轻压痕。
 * 必须给 label——图标按钮没有文字，这是无障碍的硬要求（aria-label + title）。
 */

import type { ButtonHTMLAttributes } from 'react'

import { cx } from './cx'
import { Icon, type IconName } from './Icon'

export type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & {
  icon: IconName
  label: string
  iconSize?: number
}

export function IconButton({ icon, label, iconSize = 14, className, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cx(
        'inline-flex h-[26px] w-[26px] items-center justify-center rounded-sm text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink',
        className,
      )}
      {...rest}
    >
      <Icon name={icon} size={iconSize} />
    </button>
  )
}
