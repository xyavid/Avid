/**
 * 助手消息（报告 §7.3）：头像行 + 正文分列，左对齐、无气泡（纸面直书）。
 * 头像 24px 圆，名称行（fs-ui）在其上垂直居中；正文走正文衬线栈
 * （PT Serif），字号 --chat-message-font-size，中文衬线行高 1.7（报告 §5）。
 * 流式态：有部分文本 → 正文 + accent 光标呼吸（hana-pulse）；还没有文本 →
 * 三枚 accent 呼吸点（hana-cycling-dots，阶段 3 补件关键帧）。
 */

import type { ReactNode } from 'react'

import { cx } from '../../ui/cx'
import { AvidMark } from '../../ui/Mark'

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
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-a8">
          {/* 头像：标识本身代替了原先的字母「A」。小尺寸取 accent——流式光标与呼吸点
              同为 accent，助手这一侧的颜色由此统一到一处；形状在 13px 下只剩剪影，
              靠颜色比靠轮廓更容易被认出。 */}
          <span
            aria-hidden
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-hairline border-hair bg-card text-accent"
          >
            <AvidMark size={13} />
          </span>
          <span className="font-ui text-ui leading-[18px] text-ink-light">{name}</span>
        </div>
        {streaming ? (
          children ? (
            <div className="serif-text mt-a4 text-chat text-ink">
              {children}
              <span
                aria-hidden
                className="ml-a4 inline-block h-[10px] w-[3px] translate-y-[1px] rounded-[1px] bg-accent"
                style={{ animation: 'hana-pulse 1.2s ease-in-out infinite' }}
              />
            </div>
          ) : (
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
          )
        ) : (
          <div className="serif-text mt-a4 text-chat text-ink">{children}</div>
        )}
      </div>
    </div>
  )
}
