/**
 * 助手消息（报告 §7.3）：头像行 + 正文分列，左对齐、无气泡（纸面直书）。
 * 头像 24px 圆，名称行（fs-ui）在其上垂直居中；正文走正文衬线栈
 * （PT Serif），字号 --chat-message-font-size，中文衬线行高 1.7（报告 §5）。
 * 流式态：有部分文本 → 正文 + accent 光标呼吸（hana-pulse）；还没有文本 →
 * 三枚 accent 呼吸点（hana-cycling-dots，阶段 3 补件关键帧）。
 */

import type { ReactNode } from 'react'

import { Markdown } from '../../markdown'
import { cx } from '../../ui/cx'
import { AvidMark } from '../../ui/Mark'

const DOTS = [0, 1, 2]

export type AssistantMessageProps = {
  children?: ReactNode
  streaming?: boolean
  name?: string
  className?: string
  /** 是否显示标识行（标识 + 名称）。同一轮里第二次以后的正文是续写，
   *  再挂一次名字只是噪音——调用方按「本轮出现过正文没有」决定。 */
  showHead?: boolean
}

/** 字符串走 markdown 渲染；已经是元素（或空）就原样放。 */
function Body({ children, trailing }: { children?: ReactNode; trailing?: ReactNode }) {
  if (typeof children === 'string') return <Markdown trailing={trailing}>{children}</Markdown>
  return (
    <>
      {children}
      {trailing}
    </>
  )
}

export function AssistantMessage({
  children,
  streaming = false,
  name = 'Avid',
  className,
  showHead = true,
}: AssistantMessageProps) {
  const cursor = (
    <span
      aria-hidden
      className="ml-a4 inline-block h-[10px] w-[3px] translate-y-[1px] rounded-[1px] bg-accent"
      style={{ animation: 'hana-pulse 1.2s ease-in-out infinite' }}
    />
  )
  return (
    <div className={cx('flex gap-a8', className)}>
      <div className="min-w-0 flex-1">
        {showHead && (
          <div className="flex items-center gap-a8">
            {/* 头像：标识直接贴在纸面上——不做圆托、不垫色板（用户要求背景透明），
                图案自带配色，与旁边名称行同一片底色。 */}
            <AvidMark size={24} className="shrink-0" />
            <span className="font-ui text-ui leading-[18px] text-ink-light">{name}</span>
          </div>
        )}
        {streaming ? (
          children ? (
            <div className="serif-text mt-a4 text-chat text-ink">
              <Body trailing={cursor}>{children}</Body>
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
          <div className="serif-text mt-a4 text-chat text-ink">
            <Body>{children}</Body>
          </div>
        )}
      </div>
    </div>
  )
}
