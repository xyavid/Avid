/**
 * 用户气泡（组件墙 §消息）：右对齐、accent 浅垫底、主文字色、圆角 8px。
 * 内边距 9/14 与字号取墙的解剖值（字号走 --chat-message-font-size 15px，
 * 墙演示页的 14px 被聊天区 token 取代）；上限 75% 对话列宽——参考图实量
 * 气泡约占 60%，留出余量。
 */

import type { HTMLAttributes } from 'react'

import { cx } from '../../ui/cx'

export type UserBubbleProps = HTMLAttributes<HTMLDivElement>

export function UserBubble({ className, children, ...rest }: UserBubbleProps) {
  return (
    <div className="flex justify-end">
      <div
        className={cx(
          'max-w-[75%] rounded-md bg-accent-light px-[14px] py-[9px] font-ui text-chat leading-[1.6] text-ink',
          className,
        )}
        {...rest}
      >
        {children}
      </div>
    </div>
  )
}
