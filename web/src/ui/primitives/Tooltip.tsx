import * as RadixTooltip from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'

export interface TooltipProps {
  /** 气泡内容：短说明用字符串，结构化明细（清单 / 进度条）可以直接给 JSX。 */
  label: ReactNode
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  /**
   * 气泡容器的 id（可选）。给了它以后 Radix 会用它当 `contentId`：气泡自己挂这个 id，
   * 触发元素的 `aria-describedby` 也指向它——于是调用点既能拿到一个稳定的 e2e 锚点，
   * 又不必自己重复写一遍 `aria-describedby`。
   */
  id?: string
}

/** 提示气泡：accessible name 始终来自触发元素的文字，这里只是补充说明。 */
export function Tooltip({ label, children, side = 'bottom', id }: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={300}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            id={id}
            side={side}
            sideOffset={6}
            className="surface-chip z-toast max-w-xs px-2 py-1 text-xs"
          >
            {label}
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  )
}
