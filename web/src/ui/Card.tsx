/**
 * 纸本卡片（组件墙 §卡片）：抬升面 + 0.5px 发丝线 + 衬线标题。
 * 简单卡是 5px（rounded-sm，墙实绘）；8px 的 --radius-card 留给大面板
 * （radius="md"，右栏/设置区块用，报告 §2.3）。
 * 内边距 13/15 是墙的组件解剖值（「内边距 13–15px」），离网注明出处。
 * actions（阶段 4 补件）：标题行右侧的操作区（如工作区卡的「新增」按钮）。
 */

import type { HTMLAttributes, ReactNode } from 'react'

import { cx } from './cx'

export type CardProps = HTMLAttributes<HTMLDivElement> & {
  title?: ReactNode
  actions?: ReactNode
  radius?: 'sm' | 'md'
}

export function Card({ title, actions, radius = 'sm', className, children, ...rest }: CardProps) {
  return (
    <div
      className={cx(
        'border-hairline border-hair bg-card px-[15px] py-[13px]',
        radius === 'md' ? 'rounded-card' : 'rounded-sm',
        className,
      )}
      {...rest}
    >
      {title && (
        <div className="mb-a4 flex items-center justify-between gap-a8">
          <h3 className="font-serif text-title font-medium text-ink">{title}</h3>
          {actions}
        </div>
      )}
      <div className="font-ui text-caption leading-[1.6] text-ink-light">{children}</div>
    </div>
  )
}
