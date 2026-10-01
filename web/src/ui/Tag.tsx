/**
 * 标签（组件墙 §标签）：padding 3px 9px、字号 --fs-hint（墙 11.5px）。
 * 变体只保留 token 能表达的三个：accent / danger / neutral。
 * 墙里的「墨蓝」「印章」两枚装饰变体是 Hana 的皮肤层内容，不搬——
 * Avid 需要新语义色时，回 token 层补，再在这里开变体。
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
