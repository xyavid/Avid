/** Assistant message: mark + name head row, body rendered as markdown. */

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
  /** Show the head row; continuation segments pass false so the name is not repeated. */
  showHead?: boolean
}

/** Strings render as markdown; elements (or nothing) pass through. */
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
