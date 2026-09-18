import * as RadixDialog from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'

export interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children?: ReactNode
  footer?: ReactNode
  /** 需要读屏器播报的即时内容（例如错误）。 */
  alert?: string
}

/**
 * 唯一的对话框实现：focus trap、Escape、可访问名都来自 Radix。
 * 涂鸦的那一套（外壳倾斜、内容反向抵消、粗墨边、硬阴影）在这里一次写清，
 * 业务组件不再各自手写 `fixed inset-0`（§8.7 的 Modal 缺陷正是手写 18 份）。
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  alert,
}: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-modal bg-ink/40" />
        <RadixDialog.Content className="sketch-panel tilt-shell fixed left-1/2 top-1/2 z-modal w-[min(92vw,44rem)] -translate-x-1/2 -translate-y-1/2 p-6">
          <div className="tilt-content">
            <RadixDialog.Title className="font-sketch text-lg">{title}</RadixDialog.Title>
            {description ? (
              <RadixDialog.Description className="mt-1 text-sm text-ink/70">
                {description}
              </RadixDialog.Description>
            ) : null}
            {alert ? (
              <p role="alert" className="mt-3 border-hair border-danger bg-danger-bg/30 p-2 text-sm">
                {alert}
              </p>
            ) : null}
            <div className="mt-4">{children}</div>
            {footer ? <div className="mt-6 flex justify-end gap-2">{footer}</div> : null}
            <RadixDialog.Close asChild>
              <button
                type="button"
                aria-label="close"
                className="press press-2 sketch-chip absolute -right-3 -top-3 h-8 w-8 font-sketch"
              >
                ×
              </button>
            </RadixDialog.Close>
          </div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}
