/**
 * 临时对话面板（阶段 54）：右列里从主对话岔出来的一次性支线。
 *
 * 生命周期就是面板的生命周期：挂上 → 建一个**临时会话**（内核从源会话拷一份投影消息当
 * 历史、并打上只读标记）；卸下 → 有活动运行先取消，再 `DELETE` 掉这个会话。所以
 * 「关闭后这个 session 删除消失」不是界面把东西藏起来了，是磁盘上的会话文件真没了。
 *
 * 只读是内核保证的（工具表摘掉写入工具 + 沙箱工作区只读）；模型那边由环境块里那句
 * 「这是临时对话」自己知道。面板里**不写说明性文字**（用户要求）：界面只摆操作需要的
 * 东西——这几件事实（带上几条上下文、只读、离开即删除）进 `title`，悬停才出现。
 *
 * 对话本身与主列同构：`listEntries` 拉历史 → `itemsFromEntries` 建段落 → 同一个
 * `Timeline` 渲染器；运行走同一个 `useRunStream`（第二实例，自己一条流）。差别只有：
 * 紧凑输入（没有模型选择 / 权限芯片 / 容量环），以及没有分支与「加载更早」。
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
  /** 源会话：上下文取自它打开这一刻的样子。null = 还没有选中会话。 */
  sourceSessionId: string | null
  /** 工作区根：时间线里工具行的路径相对化用。 */
  workspaceRoot: string | null
  /** 这次运行用哪个模型与哪档强度：跟随主输入区（面板里不放第二个选择器）。 */
  model: string | null
  effort: string | null
}

/** 拆掉临时会话：先让活动运行停下来，再删（会话有活动 run 时服务端会拒绝销毁）。 */
async function destroyScratch(sessionId: string, runId: string | null): Promise<void> {
  if (runId !== null) {
    await cancelRun(runId).catch(() => {})
    // 取消是异步的：给它一点时间收尾，超时就让删除失败（残留的会话在列表里可见、可手动删）
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
  // 卸载后才知道要删哪个：create 是异步的，StrictMode 下第一次挂载还会在 promise 落地前
  // 就被卸载——那时 `sessionId` 还是 null，只有这两个 ref 还知道要收拾谁。
  const idRef = useRef<string | null>(null)
  const aliveRef = useRef(true)
  const runRef = useRef<string | null>(null)
  // 收尾回调经 ref 转一手：hook 的初始化表达式里拿不到它自己的返回值
  const settledRef = useRef<() => void>(() => {})
  const live = useRunStream(sessionId, () => settledRef.current())
  settledRef.current = () => live.settle()
  runRef.current = live.runId

  useEffect(() => {
    aliveRef.current = true
    void createScratchSession(sourceSessionId ?? '')
      .then((created) => {
        idRef.current = created.id
        // 已经卸载了（StrictMode 的第一次挂载）：建了就得拆，不能留在列表里
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
