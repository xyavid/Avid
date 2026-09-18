import * as RadixTooltip from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'

export interface TooltipProps {
  label: string
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
}

/** 提示气泡：accessible name 始终来自按钮内部文字，这里只是补充说明。 */
export function Tooltip({ label, children, side = 'bottom' }: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={300}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            className="sketch-chip z-toast max-w-xs px-2 py-1 text-xs"
          >
            {label}
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  )
}
