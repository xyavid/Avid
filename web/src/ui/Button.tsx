/**
 * 按钮（组件墙 §按钮）：描边为默认变体，primary 是主行动（使用约定：每屏 ≤1）。
 * 数值出处：描边 0.5px 发丝线、圆角 5px、内边距 6px 15px、字号 --fs-ui（墙 13px）、
 * hover 是 accent 浅底的呼吸（§8.2），禁用只降透明度。
 * 内边距 6/15 是墙的组件解剖值，不在 4px 网格上——按「离网尺寸注明出处」处理。
 */

import type { ButtonHTMLAttributes } from 'react'

import { cx } from './cx'

type ButtonVariant = 'outline' | 'primary'

const BASE =
  'inline-flex items-center justify-center rounded-sm border-hairline px-[15px] py-[6px] font-ui text-ui font-medium transition-colors duration-fast ease-out disabled:opacity-40'

const VARIANTS: Record<ButtonVariant, string> = {
  outline: 'border-hair text-accent hover:bg-accent-light',
  primary: 'border-transparent bg-accent text-card hover:bg-accent-hover',
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
}

export function Button({ variant = 'outline', className, type = 'button', ...rest }: ButtonProps) {
  return <button type={type} className={cx(BASE, VARIANTS[variant], className)} {...rest} />
}
