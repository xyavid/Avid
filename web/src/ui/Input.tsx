/**
 * Single-line input: `--control-h` tall, hairline border, raised background, focus turns the
 * border accent plus the 2px ring from `shadow-focus-ring`. `bare` drops border, background and
 * height for a shell that owns them (composer), keeping only typography and placeholder color.
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
