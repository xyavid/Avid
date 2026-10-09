/**
 * Scratch panel: a one-off read-only branch off the main conversation that lives exactly as long
 * as the panel — mount creates a scratch session (the kernel copies a projection of the source
 * history), unmount cancels any live run then `DELETE`s it, so closing really removes it from disk.
 * Read-only comes from the kernel (write tools dropped, sandbox workspace read-only); the UI shows
 * no explanatory text and puts the facts (context count, read-only, delete-on-leave) in `title`.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, cancelRun, createScratchSession, deleteSession, listEntries } from '../../api/client'
import { Timeline } from '../chat/Timeline'
import type { TimelineItem } from '../../state/timeline'
import { itemsFromEntries, mergeItems } from '../../state/timeline'
import { useRunStream } from '../../state/useRunStream'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type ScratchPanelProps = {
  /** Source session: context is copied from it as of the moment the panel opens; null = none. */
  sourceSessionId: string | null
  /** Workspace root: used to relativize tool-row paths in the timeline. */
  workspaceRoot: string | null
  /** Model and effort for this run, following the main composer (no second selector here). */
  model: string | null
  effort: string | null
}

/** Tear down the scratch session: stop the live run first; the server rejects a busy delete. */
async function destroyScratch(sessionId: string, runId: string | null): Promise<void> {
  if (runId !== null) {
    await cancelRun(runId).catch(() => {})
    // Cancellation is async: retry the delete briefly, then give up (the leftover stays listed).
    for (let i = 0; i < 10; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 200))
      const gone = await deleteSession(sessionId).then(() => true).catch(() => false)
      if (gone) return
    }
    return
  }
  await deleteSession(sessionId).catch(() => {})
}

export function ScratchPanel({ sourceSessionId, workspaceRoot, model, effort }: ScratchPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [copied, setCopied] = useState(0)
  const [history, setHistory] = useState<TimelineItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  // create is async and StrictMode unmounts early; the refs remember the id and run to clean up.
  const idRef = useRef<string | null>(null)
  const aliveRef = useRef(true)
  const runRef = useRef<string | null>(null)
  // Settle callback behind a ref: the hook's initializer cannot see its own return value.
  const settledRef = useRef<() => void>(() => {})
  const live = useRunStream(sessionId, () => settledRef.current())
  settledRef.current = () => live.settle()
  runRef.current = live.runId

  useEffect(() => {
    aliveRef.current = true
    void createScratchSession(sourceSessionId ?? '')
      .then((created) => {
        idRef.current = created.id
        // Already unmounted (StrictMode's first mount): anything created must be torn down.
        if (!aliveRef.current) {
          void destroyScratch(created.id, null)
          return
        }
        setSessionId(created.id)
        setCopied(created.copied_messages)
      })
      .catch((e: unknown) => {
        if (aliveRef.current) setError(e instanceof ApiError ? e.message : String(e))
      })
    return () => {
      aliveRef.current = false
      const known = idRef.current
      idRef.current = null
      if (known !== null) void destroyScratch(known, runRef.current)
    }
  }, [sourceSessionId])

  useEffect(() => {
    if (sessionId === null) return
    let alive = true
    listEntries(sessionId, { branch: 'main', limit: 50 })
      .then((page) => {
        if (alive) setHistory(itemsFromEntries([...page.entries].reverse()))
      })
      .catch(() => {
        if (alive) setHistory([])
      })
    return () => {
      alive = false
    }
  }, [sessionId])

  const send = useCallback(() => {
    const text = draft.trim()
    if (text === '' || sessionId === null || model === null) return
    setDraft('')
    // The scratch composer takes text only: the image entry lives in the main composer.
    void live.send(text, false, model, 'main', effort)
  }, [draft, effort, live, model, sessionId])

  const busy = live.phase === 'starting' || live.phase === 'running' || live.phase === 'settling'
  const shown = mergeItems(history ?? [], live.items)

  return (
    <div
      className="flex h-full min-h-0 flex-col"
      data-testid="scratch-panel"
      title={
        error !== null
          ? undefined
          : `已带上主对话的 ${copied} 条上下文；只读，改不了文件；离开这个面板即删除这次对话`
      }
    >
      {error !== null && (
        <p className="border-b border-hair px-a12 py-a8 font-ui text-hint text-danger">{error}</p>
      )}

      <div className="scroll-auto min-h-0 flex-1 overflow-y-auto px-a12 py-a12">
        {history === null && error === null ? (
          <p className="font-ui text-hint text-ink-muted">加载中…</p>
        ) : shown.length === 0 ? null : (
          <Timeline
            testId="timeline-scratch"
            items={shown}
            sessionId={sessionId}
            workspaceRoot={workspaceRoot}
            liveTail={busy}
          />
        )}
      </div>

      {live.approvals.length > 0 && (
        <p
          className="border-t border-hair px-a12 py-a6 font-ui text-hint text-coral"
          title="临时对话里没有答复审批的入口，请到主对话输入区上方那条处理"
        >
          {`${live.approvals.length} 条待决审批`}
        </p>
      )}

      <div className="border-t border-hair p-a8">
        <textarea
          aria-label="临时对话输入"
          rows={2}
          value={draft}
          disabled={sessionId === null || error !== null}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
          placeholder="临时问一句…（Enter 发送，Shift+Enter 换行）"
          className="w-full resize-none rounded-input border-hairline border-hair bg-card px-a8 py-a6 font-ui text-ui text-ink outline-none placeholder:text-ink-muted focus:border-accent"
        />
        <div className="mt-a6 flex items-center justify-between">
          <span
            className="flex items-center gap-a4 font-ui text-micro text-ink-muted"
            title="临时对话：只读（改不了文件），离开这个面板即删除"
          >
            <Icon name="message-square" size={11} />
            只读支线
          </span>
          {busy ? (
            <button
              type="button"
              onClick={() => void live.stop()}
              className="rounded-sm border-hairline border-hair px-a10 py-[3px] font-ui text-caption text-ink transition-colors duration-fast ease-out hover:bg-overlay-light"
            >
              停止
            </button>
          ) : (
            <button
              type="button"
              onClick={send}
              disabled={draft.trim() === '' || sessionId === null || model === null}
              className={cx(
                'rounded-sm px-a10 py-[3px] font-ui text-caption transition-colors duration-fast ease-out',
                draft.trim() === '' || sessionId === null || model === null
                  ? 'bg-overlay-light text-ink-muted'
                  : 'bg-accent text-card hover:bg-accent-hover',
              )}
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
