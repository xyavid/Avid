/**
 * Card: raised surface, hairline border, serif title. `radius="sm"` is 5px and the default,
 * `radius="md"` maps to `--radius-card` (8px) for large panels; padding 13/15px is off the grid.
 */

import type { HTMLAttributes, ReactNode } from 'react'

import { cx } from './cx'

export type CardProps = HTMLAttributes<HTMLDivElement> & {
  title?: ReactNode
  actions?: ReactNode
  radius?: 'sm' | 'md'
}

export function Card({ title, actions, radius = 'sm', className, children, ...rest }: CardProps) {
  return (
    <div
      className={cx(
        'border-hairline border-hair bg-card px-[15px] py-[13px]',
        radius === 'md' ? 'rounded-card' : 'rounded-sm',
        className,
      )}
      {...rest}
    >
      {title && (
        <div className="mb-a4 flex items-center justify-between gap-a8">
          <h3 className="font-serif text-title font-medium text-ink">{title}</h3>
          {actions}
        </div>
      )}
      <div className="font-ui text-caption leading-[1.6] text-ink-light">{children}</div>
    </div>
  )
}
