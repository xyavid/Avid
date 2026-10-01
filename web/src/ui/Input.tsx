/**
 * 单行输入框（组件墙 §输入框）：高度 --control-h(34px)、0.5px 发丝线、抬升面底，
 * focus 时边框转 accent + 2px accent-light 光晕（经 boxShadow.focus-ring）。
 * bare 变体（阶段 4 补件）：composer 的 16px 圆角壳自己负责描边与底色，
 * 壳内的输入框只保留排版与 placeholder 色。
 */

import type { InputHTMLAttributes } from 'react'

import { cx } from './cx'

const SOLID =
  'h-control w-full rounded-sm border-hairline border-hair bg-card px-[11px] font-ui text-ui text-ink transition-[border-color,box-shadow] duration-fast ease-out placeholder:text-ink-muted focus:border-accent focus:shadow-focus-ring focus:outline-none disabled:opacity-40'

const BARE =
  'w-full bg-transparent font-ui text-ui text-ink placeholder:text-ink-muted focus:outline-none disabled:opacity-40'

export type InputProps = InputHTMLAttributes<HTMLInputElement> & { bare?: boolean }

export function Input({ className, bare = false, type = 'text', ...rest }: InputProps) {
  return <input type={type} className={cx(bare ? BARE : SOLID, className)} {...rest} />
}
