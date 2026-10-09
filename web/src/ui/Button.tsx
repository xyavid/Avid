/** Button: outline is the default variant, primary is the main action (at most one per screen). */

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
