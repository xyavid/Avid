/**
 * 助手消息（报告 §7.3）：头像行 + 正文分列，左对齐、无气泡（纸面直书）。
 * 正文走正文衬线栈（PT Serif，--font-serif-text），字号 --chat-message-font-size，
 * 中文衬线行高 1.7（报告 §5）。头像 20px 圆（参考图实量）+ 名称行 fs-ui。
 * 流式态：三枚 accent 呼吸点（hana-cycling-dots，阶段 3 补件关键帧），
 * 正文此时不渲染——半截文本宁可不出现在 durable 视图里。
 */

import type { ReactNode } from 'react'

import { cx } from '../../ui/cx'

const DOTS = [0, 1, 2]

export type AssistantMessageProps = {
  children?: ReactNode
  streaming?: boolean
  name?: string
  className?: string
}

export function AssistantMessage({ children, streaming = false, name = 'Avid', className }: AssistantMessageProps) {
  return (
    <div className={cx('flex gap-a8', className)}>
      <span
        aria-hidden
        className="mt-[2px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-hairline border-hair bg-card font-serif text-micro text-ink-light"
      >
        {name.slice(0, 1)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-ui text-ui text-ink-light">{name}</div>
        {streaming ? (
          <span role="status" aria-label="生成中" className="mt-a4 inline-flex items-center gap-[3px]">
            {DOTS.map((i) => (
              <span
                key={i}
                className="h-[4px] w-[4px] rounded-full bg-accent"
                style={{
                  animation: 'hana-cycling-dots 1.2s var(--ease-standard) infinite',
                  animationDelay: `${i * 0.2}s`,
                }}
              />
            ))}
          </span>
        ) : (
          <div className="serif-text mt-a2 text-chat text-ink">{children}</div>
        )}
      </div>
    </div>
  )
}
