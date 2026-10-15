/**
 * The session's own checkout, one line under the composer: which tree and which git branch its
 * runs work on, with the path a click away.
 *
 * Rendered only when the session has a tree — a session running on the workspace root has nothing
 * extra to say here (the right-hand panel states the reason when asked).
 */

import { useState } from 'react'

import type { WorktreeFacts } from '../../api/types'
import { copyText } from '../../markdown/clipboard'

export function WorktreeStrip({ facts }: { facts: WorktreeFacts | null }) {
  const [copied, setCopied] = useState(false)
  if (facts === null || !facts.worktree || facts.branch === null) return null

  return (
    <div className="mx-auto flex w-full max-w-chat-input items-center justify-between gap-a8 border-t border-hair px-a16 py-a6">
      <span className="min-w-0 truncate font-ui text-hint text-ink-muted">
        工作树 <span className="font-mono">{facts.path}</span>（分支 {facts.branch}）
      </span>
      <button
        type="button"
        aria-label="复制工作树路径"
        title="复制工作树路径"
        onClick={() => {
          void copyText(facts.path).then((ok) => {
            if (!ok) return
            setCopied(true)
            window.setTimeout(() => setCopied(false), 1500)
          })
        }}
        className="shrink-0 rounded-sm px-a8 py-[2px] font-ui text-hint text-accent transition-colors duration-fast ease-out hover:bg-overlay-light"
      >
        {copied ? '已复制' : '复制路径'}
      </button>
    </div>
  )
}
