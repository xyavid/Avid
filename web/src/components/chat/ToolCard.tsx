/**
 * 工具卡（组件墙 §工具卡）：330px 宽、抬升面、0.5px 发丝线、微阴影，
 * 头部（工具图标 accent 色 + 标题 + 右侧徽章）与等宽小字正文之间以发丝线分隔。
 * 宽度 330px 是墙的解剖值，与主界面参考图实量一致。
 * 图标按工具类型传：file-frame（文件类）/ terminal（bash）/ globe（web_search）…
 */

import type { HTMLAttributes, ReactNode } from 'react'

import { Badge } from '../../ui/Badge'
import { cx } from '../../ui/cx'
import { Icon, type IconName } from '../../ui/Icon'

export type ToolCardProps = HTMLAttributes<HTMLDivElement> & {
  icon: IconName
  title: string
  badge?: ReactNode
}

export function ToolCard({ icon, title, badge, className, children, ...rest }: ToolCardProps) {
  return (
    <div
      className={cx(
        'w-[330px] overflow-hidden rounded-card border-hairline border-hair bg-card shadow-soft',
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
      </div>
      <div className="px-a12 py-[9px] font-mono text-micro leading-[1.6] text-ink-muted">{children}</div>
    </div>
  )
}
