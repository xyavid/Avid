/**
 * Right-hand panel: the session's own checkout — where it is, what it changed, what it committed,
 * and the command that brings it back into the workspace's checkout.
 *
 * Presentational only: the page owns the fetch and hands the reading down (the dock convention),
 * and a reading with ``worktree`` false states the reason instead of showing empty lists.
 */

import { useState } from 'react'

import type { WorktreeFacts } from '../../api/types'
import { copyText } from '../../markdown/clipboard'

export type WorktreePanelProps = {
  facts: WorktreeFacts | null
  loading: boolean
  error: string | null
  /** The workspace's checkout: where the merge command runs. */
  workspaceRoot: string | null
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => {
        void copyText(text).then((ok) => {
          if (!ok) return
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        })
      }}
      className="shrink-0 rounded-sm px-a8 py-[2px] font-ui text-hint text-accent transition-colors duration-fast ease-out hover:bg-overlay-light"
    >
      {copied ? '已复制' : '复制'}
    </button>
  )
}

export function WorktreePanel({ facts, loading, error, workspaceRoot }: WorktreePanelProps) {
  if (error !== null) {
    return <p className="p-a12 font-ui text-ui text-danger">{error}</p>
  }
  if (facts === null) {
    return (
      <p className="p-a12 font-ui text-hint text-ink-muted">{loading ? '读取中…' : '没有读数'}</p>
    )
  }

  const merge =
    facts.branch !== null && workspaceRoot !== null
      ? `git -C ${workspaceRoot} merge ${facts.branch}`
      : null

  return (
    <div className="flex flex-col gap-a12 p-a12 font-ui text-ui text-ink">
      <section className="flex flex-col gap-a4">
        <span className="text-hint text-ink-muted">
          {facts.worktree ? '会话在自己的工作树上' : '会话直接在工作区上跑'}
        </span>
        <span className="flex items-center gap-a4">
          <span className="min-w-0 truncate font-mono text-hint">{facts.path}</span>
          <CopyButton text={facts.path} label="复制工作树路径" />
        </span>
        {facts.branch !== null && <span className="text-hint text-ink-muted">分支 {facts.branch}</span>}
        {facts.reason !== null && <span className="text-hint text-ink-muted">{facts.reason}</span>}
        {facts.note !== null && <span className="text-hint text-danger">{facts.note}</span>}
      </section>

      {facts.worktree && (
        <>
          <section className="flex flex-col gap-a4">
            <span className="text-hint text-ink-muted">
              改动 {facts.dirty.length}
              {facts.dirty.length > 0 && '（未提交）'}
            </span>
            {facts.dirty.map((file) => (
              <span key={`${file.status}:${file.path}`} className="flex items-center gap-a6">
                <span className="shrink-0 font-mono text-hint text-ink-muted">{file.status}</span>
                <span className="min-w-0 truncate font-mono text-hint">{file.path}</span>
              </span>
            ))}
            {facts.dirty.length === 0 && <span className="text-hint text-ink-muted">没有改动</span>}
          </section>

          <section className="flex flex-col gap-a4">
            <span className="text-hint text-ink-muted">
              提交 {facts.commits.length}
              {facts.ahead !== null && `（领先主检出 ${facts.ahead} 个提交）`}
            </span>
            {facts.commits.map((commit) => (
              <span key={commit.sha} className="flex items-baseline gap-a6">
                <span className="shrink-0 font-mono text-hint text-ink-muted">{commit.sha}</span>
                <span className="min-w-0 truncate">{commit.subject}</span>
              </span>
            ))}
            {facts.commits.length === 0 && (
              <span className="text-hint text-ink-muted">这个会话还没提交过</span>
            )}
          </section>

          {merge !== null && (
            <section className="flex flex-col gap-a4">
              <span className="text-hint text-ink-muted">把这个会话的提交并回主检出</span>
              <span className="flex items-center gap-a4">
                <span className="min-w-0 truncate font-mono text-hint">{merge}</span>
                <CopyButton text={merge} label="复制合并命令" />
              </span>
            </section>
          )}
        </>
      )}
    </div>
  )
}
