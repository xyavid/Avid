/**
 * Icon button: 26px, ghost is the default and primary is the accent-filled variant.
 * `label` is required and feeds both `aria-label` and `title` — an icon-only control has
 * no other accessible name.
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
