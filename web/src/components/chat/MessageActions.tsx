/**
 * 消息动作行（阶段 14）：悬停在一条消息上时，消息**底部**浮出一行小动作。
 *
 * 两条纪律：
 *   · **隐藏直到需要**——平时 `opacity-0`，鼠标悬停或键盘聚焦才淡入（和会话项上的
 *     置顶/归档同一套），免得每一轮对话下面都挂着一排按钮；
 *   · **用户消息没有「分支」**——分叉点是模型的回答（从那里再问一遍、换个方向继续），
 *     用户自己的话分叉没有意义。组件用「有没有给 onBranch」表达这件事，调用方不传即没有。
 *
 * 复制复制的是**原文**（助手侧是 markdown 源码，不是渲染后的文本）——粘到别处仍然成形。
 */

import { useState } from 'react'

import { copyText } from '../../markdown'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type MessageActionsProps = {
  /** 要复制的原文。 */
  text: string
  /** 给了才显示「分支」；参数无（分叉点由调用方闭包带上）。 */
  onBranch?: () => void
  className?: string
}

export function MessageActions({ text, onBranch, className }: MessageActionsProps) {
  const [copied, setCopied] = useState(false)

  return (
    <div
      data-testid="message-actions"
      className={cx(
        'mt-a4 flex items-center gap-a2 opacity-0 transition-opacity duration-fast ease-out group-hover:opacity-100 group-focus-within:opacity-100',
        className,
      )}
    >
      <ActionButton
        icon="copy"
        label={copied ? '已复制' : '复制'}
        onClick={() => {
          void copyText(text).then((ok) => {
            if (!ok) return
            setCopied(true)
            window.setTimeout(() => setCopied(false), 1500)
          })
        }}
      />
      {onBranch && <ActionButton icon="git-branch" label="分支" onClick={onBranch} />}
    </div>
  )
}

function ActionButton({ icon, label, onClick }: { icon: 'copy' | 'git-branch'; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-sm text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
    >
      <Icon name={icon} size={13} />
    </button>
  )
}
