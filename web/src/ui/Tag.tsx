/**
 * Tag: padding 3px/9px, `--fs-hint` label; variants stay limited to what tokens can express
 * (accent / danger / neutral) — a new semantic color is added to the token layer first.
 */

import type { HTMLAttributes } from 'react'

import { cx } from './cx'

type TagVariant = 'accent' | 'danger' | 'neutral'

const VARIANTS: Record<TagVariant, string> = {
  accent: 'bg-accent-light text-accent-hover',
  danger: 'bg-danger/[0.08] text-danger',
  neutral: 'bg-sidebar text-ink-light',
}

export type TagProps = HTMLAttributes<HTMLSpanElement> & { variant?: TagVariant }

export function Tag({ variant = 'accent', className, ...rest }: TagProps) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-sm px-[9px] py-[3px] font-ui text-hint font-medium',
        VARIANTS[variant],
        className,
      )}
      {...rest}
    />
  )
}
