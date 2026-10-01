/**
 * 徽章（组件墙 §徽章）：带可选线性图标的短标签。padding 2px 8px 全在 4px
 * 网格上（py-a2 px-a8）；gap 5px 是墙的解剖值。
 */

import type { HTMLAttributes } from 'react'

import { cx } from './cx'
import { Icon, type IconName } from './Icon'

type BadgeVariant = 'accent' | 'danger'

const VARIANTS: Record<BadgeVariant, string> = {
  accent: 'bg-accent-light text-accent-hover',
  danger: 'bg-danger/[0.08] text-danger',
}

export type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  variant?: BadgeVariant
  icon?: IconName
}

export function Badge({ variant = 'accent', icon, className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-[5px] rounded-sm px-a8 py-a2 font-ui text-hint font-medium',
        VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {icon ? <Icon name={icon} /> : null}
      {children}
    </span>
  )
}
