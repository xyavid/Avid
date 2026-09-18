import { forwardRef } from 'react'
import { clsx } from 'clsx'
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

export interface FieldProps {
  label: string
  hint?: string
  error?: string
  htmlFor?: string
  children: ReactNode
}

/** 表单行的统一形状：标签、提示、错误（错误用 role=alert）。 */
export function Field({ label, hint, error, htmlFor, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="font-sketch text-xs text-ink/70">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-ink/70">{hint}</p> : null}
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}

const CONTROL =
  'min-h-control w-full rounded-chip border-hair border-ink bg-input px-3 py-2 text-sm text-ink'

/**
 * ``forwardRef`` 是为了"打开就能打字"这类焦点管理（导航列的搜索框用它）。
 * React 18 里 ref 不是普通 prop，所以只有在这里显式转发。
 */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} {...rest} className={clsx(CONTROL, className)} />
  },
)

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={clsx(CONTROL, 'resize-y leading-relaxed', className)} />
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={clsx(CONTROL, 'font-sketch', className)}>
      {children}
    </select>
  )
}
