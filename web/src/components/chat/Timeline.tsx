/**
 * Renders ordered `TimelineItem`s into the conversation column; it never touches the wire
 * format — `state/timeline.ts` owns where segments come from and the fold decision
 * (`turnGroups`). `liveTail` is the one exception that keeps the running turn expanded;
 * in-turn order is event arrival: thinking → text → the tools it triggered.
 */

import { useState } from 'react'
import type { ReactNode } from 'react'

import { attachmentUrl } from '../../api/client'
import type { TimelineImage, TimelineItem, TimelinePending } from '../../state/timeline'
import { cx } from '../../ui/cx'
import { itemKey, subagentSteps, turnGroups } from '../../state/timeline'
import { AssistantMessage } from './AssistantMessage'
import { MessageActions } from './MessageActions'
import { ReasoningBlock } from './ReasoningBlock'
import { ToolCard } from './ToolCard'
import { TurnSummary } from './TurnSummary'
import { toolDetail } from './toolDetail'
import { toolLabel } from './toolLabel'
import type { UserBubbleImage } from './UserBubble'
import { UserBubble } from './UserBubble'

export type TimelineProps = {
  items: TimelineItem[]
  /** Container data-testid; the subagent panel passes a different one so acceptance
   *  scripts can tell the two timelines apart. */
  testId?: string
  /** Workspace root for relativizing tool paths; null = absolute paths stay as-is. */
  workspaceRoot?: string | null
  /** Fork from this message; only persisted assistant messages (streaming has no entry_id). */
  onBranch?: (entryId: string) => void
  /** The last turn is still running: it never folds. */
  liveTail?: boolean
  /** Clicking a subagent card notifies the caller (opens the right-column subagents panel). */
  onOpenSubagents?: () => void
  /**
   * Entry jumped to from a search hit: highlight and scroll into view once; re-paging when
   * it is not on the current page is the assembly layer's job.
   */
  focusEntry?: string | null
  /** Session id used to compose stored-image URLs; null = stored images are not drawn. */
  sessionId?: string | null
  /** Drop a pending input; omit to hide the drop button. */
  onDropInput?: (inputId: string) => void
}

/**
 * One action row per assistant reply, on its last text segment; copied text joins the whole
 * reply and the branch point is that persisted segment (streaming has no entry_id).
 */
function turnActions(items: TimelineItem[]): Map<number, { text: string; branchAt: string | null }> {
  const actions = new Map<number, { text: string; branchAt: string | null }>()
  let texts: string[] = []
  let last: number | null = null
  const flush = () => {
    if (last !== null) {
      const item = items[last]
      actions.set(last, {
        text: texts.join('\n\n'),
        branchAt: item?.kind === 'assistant' ? item.entryId : null,
      })
    }
    texts = []
    last = null
  }
  items.forEach((item, index) => {
    if (item.kind === 'user') {
      flush()
      return
    }
    if (item.kind === 'assistant') {
      texts.push(item.text)
      last = index
    }
  })
  flush()
  return actions
}

type TurnAction = { text: string; branchAt: string | null }

/** Search-hit highlight, reusing the input focus mark `shadow-focus-ring`. */
function isFocused(entryId: string | null | undefined, focus: string | null): string {
  return entryId && focus && entryId === focus
    ? 'rounded-sm bg-accent-light/40 p-a4 shadow-focus-ring'
    : ''
}

type Ctx = {
  workspaceRoot: string | null
  onBranch?: (entryId: string) => void
  onOpenSubagents?: () => void
  /** Entry focused from search; null = none. */
  focusEntry: string | null
  /** Consumed by the turn's first text segment so it takes the head row. */
  head: { pending: boolean }
  /** Whole-reply actions (copy / branch) indexed by the segment's index within its group. */
  actions: Map<number, TurnAction>
  /** Session id for composing stored-image URLs; null = no session selected yet. */
  sessionId: string | null
  /** Drop an unclaimed pending input; omit to hide the drop button. */
  onDropInput?: (inputId: string) => void
}

function pendingLabel(pending: TimelinePending): string {
  if (pending.missed) return '没赶上，已排队'
  return pending.mode === 'now' ? '插入中 · 下一个 step' : '排队中 · 下一轮'
}

/** Item images → bubble (src, name): object URLs for drafts, the read endpoint for stored ones. */
function bubbleImages(images: TimelineImage[] | undefined, sessionId: string | null): UserBubbleImage[] {
  if (!images || images.length === 0) return []
  return images.flatMap((image) => {
    if (image.source === 'local') return [{ src: image.url, name: image.name }]
    if (sessionId === null) return [] // no session, no readable URL: skip rather than draw broken
    return [{ src: attachmentUrl(sessionId, image.entryId, image.index), name: image.name }]
  })
}

/** Item → node; `list` is the part to draw after folding (a whole turn or just the process)
 *  and `offset` is its start index within the group (keys and actions are group-indexed). */
function itemNodes(list: TimelineItem[], offset: number, ctx: Ctx): ReactNode[] {
  return list.map((item, index) => {
    const at = offset + index
    const key = itemKey(item, at)
    // Consecutive tool rows stick together: one action group needs no paragraph gap between rows.
    const tight = item.kind === 'tool' && list[index - 1]?.kind === 'tool'

    if (item.kind === 'user') {
      ctx.head.pending = true
      return (
        <div
          key={key}
          className={cx('group', isFocused(item.entryId, ctx.focusEntry))}
          data-item="user"
          data-entry={item.entryId ?? undefined}
          data-focus={item.entryId === ctx.focusEntry ? 'true' : undefined}
        >
          {item.pending && (
            <p
              className="mb-a4 flex items-center justify-end gap-a6 font-ui text-hint text-ink-muted"
              data-testid="pending-input"
              data-input={item.pending.inputId}
              data-missed={item.pending.missed ? 'true' : undefined}
            >
              {pendingLabel(item.pending)}
              {item.pending.images > 0 && <span>（{item.pending.images} 张图）</span>}
              {ctx.onDropInput && (
                <button
                  type="button"
                  onClick={() => ctx.onDropInput?.(item.pending!.inputId)}
                  aria-label="撤销这条补充"
                  className="text-ink-muted transition-colors duration-fast ease-out hover:text-ink"
                >
                  撤销
                </button>
              )}
            </p>
          )}
          <UserBubble images={bubbleImages(item.images, ctx.sessionId)}>{item.text}</UserBubble>
          <MessageActions text={item.text} className="justify-end" />
        </div>
      )
    }

    if (item.kind === 'assistant') {
      const showHead = ctx.head.pending
      ctx.head.pending = false
      const turn = ctx.actions.get(at)
      return (
        <div
          key={key}
          className={cx('group', isFocused(item.entryId, ctx.focusEntry))}
          data-item="assistant"
          data-entry={item.entryId ?? undefined}
          data-focus={item.entryId === ctx.focusEntry ? 'true' : undefined}
        >
          <AssistantMessage streaming={item.streaming} showHead={showHead}>
            {item.text}
          </AssistantMessage>
          {turn && <MessageActions text={turn.text} onBranch={branchHandler(turn.branchAt, ctx.onBranch)} />}
        </div>
      )
    }

    if (item.kind === 'error') {
      // Error item: a narrow danger strip, no bubble and no actions — it is not something said.
      return (
        <div
          key={key}
          data-item="error"
          data-entry={item.entryId ?? undefined}
          data-focus={item.entryId === ctx.focusEntry ? 'true' : undefined}
          className={cx(
            'rounded-sm border-hairline border-hair bg-danger/5 px-a8 py-a4 font-ui text-hint leading-[1.7] text-danger',
            isFocused(item.entryId, ctx.focusEntry),
          )}
        >
          {item.text}
        </div>
      )
    }

    if (item.kind === 'reasoning') {
      return (
        <div key={key} data-item="reasoning">
          <ReasoningBlock
            text={item.text}
            streaming={item.streaming}
            durationMs={item.endedAt - item.startedAt}
          />
        </div>
      )
    }

    const label = toolLabel(item.name, item.args, ctx.workspaceRoot)
    // File-tool detail needs only args + result, so the stream and a reload draw the same thing.
    const detail = toolDetail(item.name, item.args, item.result, item.status)
    return (
      // data-* is the e2e read path: item kind and identity live on the DOM, not on class names.
      <div key={key} data-item="tool" data-call={item.callId} className={tight ? '-mt-a8' : undefined}>
        <ToolCard
          icon={label.icon}
          verb={label.verb}
          target={label.target}
          status={item.status}
          durationMs={item.durationMs}
          steps={subagentSteps(item)}
          workspaceRoot={ctx.workspaceRoot}
          detail={detail}
          onOpen={item.name === 'subagent' ? ctx.onOpenSubagents : undefined}
        >
          <span className="line-clamp-6 block whitespace-pre-wrap">{item.result ?? item.args}</span>
        </ToolCard>
      </div>
    )
  })
}

export function Timeline({
  items,
  testId = 'timeline',
  workspaceRoot = null,
  onBranch,
  liveTail = false,
  onOpenSubagents,
  focusEntry = null,
  sessionId = null,
  onDropInput,
}: TimelineProps) {
  // Manually expanded turns; folding is the default and a session switch or reload resets this.
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set())
  const groups = turnGroups(items)

  const toggle = (key: string) => {
    setOpened((cur) => {
      const next = new Set(cur)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const nodes: ReactNode[] = []
  groups.forEach((group, index) => {
    const running = liveTail && index === groups.length - 1
    // Foldable needs a final answer, a process to fold, and not the running turn.
    const foldable = group.answer !== null && group.process.length > 0 && !running
    const open = foldable && opened.has(group.key)
    const ctx: Ctx = {
      focusEntry,
      workspaceRoot,
      onBranch,
      onOpenSubagents,
      head: { pending: true },
      actions: turnActions(group.items),
      sessionId,
      onDropInput,
    }
    const from = group.user === null ? 0 : 1

    if (group.user !== null) nodes.push(...itemNodes([group.user], group.offset, ctx))
    if (foldable) {
      // The summary row heads the turn's process: everything when closed, toggles the rest.
      nodes.push(
        <TurnSummary
          key={`${group.key}:summary`}
          durationMs={group.durationMs}
          open={open}
          onToggle={() => toggle(group.key)}
        />,
      )
      if (open) nodes.push(...itemNodes(group.process, from, ctx))
    } else {
      nodes.push(...itemNodes(group.process, from, ctx))
    }
    if (group.answer !== null) {
      nodes.push(...itemNodes([group.answer], group.items.length - 1, ctx))
    }
  })

  return (
    <div className="flex flex-col gap-a16" data-testid={testId}>
      {nodes}
    </div>
  )
}

function branchHandler(entryId: string | null, onBranch?: (entryId: string) => void): (() => void) | undefined {
  if (entryId === null || onBranch === undefined) return undefined
  return () => onBranch(entryId)
}
