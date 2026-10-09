/**
 * Hover-revealed action row under a message; copy takes the original text (markdown source
 * on the assistant side). The branch button only exists when `onBranch` is passed — user
 * messages have no fork point.
 */

import { useState } from 'react'

import { copyText } from '../../markdown'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type MessageActionsProps = {
  text: string
  /** Given, the branch button appears; the fork point travels in the caller's closure. */
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
