/**
 * 工具卡（组件墙 §工具卡）双形态：
 * - **折叠态（默认）**：单行摘要——accent 工具图标 + 工具名 + 参数/结果预览
 *   （等宽小字、截断）+ 状态标记（成功勾 / 失败叉 / 运行中呼吸点），点击展开；
 * - **展开态**：完整卡——头部（图标 + 标题 + 徽章 + 收起钮）与等宽正文，发丝线分隔，
 *   宽度 330px 是墙的解剖值，微阴影。
 * 失败判定与后端 classify_tool_status 同口径（schemas.py：'错误：'/'参数错误：'
 * 前缀，或前 64 字符含 '执行失败：'）——口径改动要两侧同步。
 */

import { useState } from 'react'
import type { HTMLAttributes, ReactNode } from 'react'

import { Badge } from '../../ui/Badge'
import { cx } from '../../ui/cx'
import { Icon, type IconName } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'

export type ToolStatus = 'ok' | 'failed' | 'running'

function StatusMark({ status }: { status: ToolStatus }) {
  if (status === 'ok') {
    return (
      <span aria-label="成功" className="shrink-0 text-ok">
        <Icon name="check" size={12} />
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span aria-label="失败" className="shrink-0 text-danger">
        <Icon name="x" size={12} />
      </span>
    )
  }
  return (
    <span
      aria-label="运行中"
      className="inline-block h-[5px] w-[5px] shrink-0 rounded-full bg-accent"
      style={{ animation: 'hana-pulse 1.6s ease-in-out infinite' }}
    />
  )
}

export type ToolCardProps = HTMLAttributes<HTMLElement> & {
  icon: IconName
  title: string
  badge?: ReactNode
  /** 折叠行的摘要：结果首行（有结果）或参数压缩串（未完/中断）。 */
  preview?: string
  status?: ToolStatus
  defaultExpanded?: boolean
}

export function ToolCard({
  icon,
  title,
  badge,
  preview,
  status = 'running',
  defaultExpanded = false,
  className,
  children,
  ...rest
}: ToolCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className={cx(
          'flex w-[330px] max-w-full items-center gap-a8 rounded-sm border-hairline border-hair bg-card px-a8 py-[5px] text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
          className,
        )}
        {...rest}
      >
        <span className="shrink-0 text-accent">
          <Icon name={icon} size={13} />
        </span>
        <span className="shrink-0 truncate font-ui text-ui text-ink">{title}</span>
        {preview && (
          <span className="min-w-0 flex-1 truncate font-mono text-hint text-ink-muted">{preview}</span>
        )}
        <StatusMark status={status} />
      </button>
    )
  }

  return (
    <div
      className={cx(
        'w-[330px] max-w-full overflow-hidden rounded-card border-hairline border-hair bg-card shadow-soft',
        className,
      )}
      {...rest}
    >
      <div className="flex items-center gap-a8 border-b border-hairline border-hair px-a12 py-a8 font-ui text-caption text-ink-light">
        <span className="shrink-0 text-accent">
          <Icon name={icon} size={13} />
        </span>
        <span className="truncate">{title}</span>
        {badge && (
          <span className="ml-auto shrink-0">
            <Badge>{badge}</Badge>
          </span>
        )}
        <IconButton
          icon="chevron-down"
          label="收起"
          iconSize={12}
          onClick={() => setExpanded(false)}
          className={badge ? undefined : 'ml-auto'}
        />
      </div>
      <div className="px-a12 py-[9px] font-mono text-micro leading-[1.6] text-ink-muted">{children}</div>
    </div>
  )
}
