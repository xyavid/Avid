import type { ReactNode } from 'react'

import { useViewport } from './useViewport'

export interface InspectorSlotProps {
  children: ReactNode
}

/** 检查器的落位：宽屏是第三列，其余档位是右侧抽屉覆盖层。 */
export function InspectorSlot({ children }: InspectorSlotProps) {
  const { isWide } = useViewport()
  return (
    <div
      className={
        isWide ? 'w-[26rem] shrink-0' : 'fixed inset-y-3 right-3 z-drawer w-[min(92vw,26rem)]'
      }
    >
      {children}
    </div>
  )
}
