/**
 * 工具组（参考图「N 个工具」）：同一轮的连续工具调用聚在一组，组头可开关
 * 整组——**默认折叠**（只显示「N 个工具」组头），点开后逐条折叠行。组内
 * 每条仍是独立的工具卡，可单独点开展开成完整卡。
 */

import { useState } from 'react'
import type { ReactNode } from 'react'

import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ToolGroupProps = {
  count: number
  children: ReactNode
}

export function ToolGroup({ count, children }: ToolGroupProps) {
  const [open, setOpen] = useState(false)

  return (
    <div className="flex flex-col gap-a8">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-[330px] max-w-full items-center justify-between rounded-sm border-hairline border-hair bg-card px-a8 py-[5px] font-ui text-ui text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
      >
        <span>{count} 个工具</span>
        <span className={cx('text-ink-muted transition-transform duration-fast ease-out', open ? '' : '-rotate-90')}>
          <Icon name="chevron-down" size={12} />
        </span>
      </button>
      {open && children}
    </div>
  )
}
