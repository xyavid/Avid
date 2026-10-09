/**
 * Subagent panel: two levels, like the files panel — a list of task cards ↔ the selected run's own
 * timeline, rendered with the same `components/chat/Timeline` model as the parent conversation.
 * A sub-run's detail is not persisted (only its task list survives a reload), so the panel shows
 * no explanatory text and puts such facts in `title` instead.
 */

import { useState } from 'react'

import type { SubagentRunView } from '../../state/timeline'
import { Icon } from '../../ui/Icon'
import { cx } from '../../ui/cx'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { Timeline } from '../chat/Timeline'

export type SubagentPanelProps = {
  /** Sub-runs flattened from the whole timeline, in dispatch order. */
  runs: SubagentRunView[]
  /** Whether the current turn is still running: drives the running mark. */
  live: boolean
  workspaceRoot: string | null
}

/** Tool steps drawn for one sub-run; the list reports only this, never success or failure. */
function stepCount(run: SubagentRunView): number {
  return run.items.filter((item) => item.kind === 'tool').length
}

function StatusMark({ running }: { running: boolean }) {
  if (!running) return null
  return (
    <span
      aria-label="运行中"
      className="h-[5px] w-[5px] shrink-0 rounded-full bg-accent"
      style={{ animation: 'hana-pulse 1.6s ease-in-out infinite' }}
    />
  )
}

export function SubagentPanel({ runs, live, workspaceRoot }: SubagentPanelProps) {
  const scrollRef = useAutoHideScroll<HTMLDivElement>()
  // Selection is panel-local state (the page does not care); `null` = showing the list.
  const [selected, setSelected] = useState<number | null>(null)
  const run = selected === null ? null : (runs[selected] ?? null)

  if (runs.length === 0) {
    return <p className="p-a12 font-ui text-hint text-ink-muted">还没有子智能体</p>
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-a6 border-b border-hair px-a8 py-a6">
        {run === null ? (
          <span className="flex-1 px-a4 font-ui text-hint text-ink-muted">
            {`${runs.length} 个子任务${live ? ' · 运行中' : ''}`}
          </span>
        ) : (
          <button
            type="button"
            aria-label="回到子任务列表"
            title="子任务列表"
            onClick={() => setSelected(null)}
            className="flex min-w-0 flex-1 items-center gap-a6 rounded-sm px-a4 py-a6 text-left transition-colors duration-fast ease-out hover:bg-overlay-light"
          >
            <span aria-hidden className="shrink-0 rotate-90 text-ink-muted">
              <Icon name="chevron-down" size={12} />
            </span>
            <span className="min-w-0 truncate font-ui text-ui text-ink">{run.task}</span>
          </button>
        )}
      </div>

      <div ref={scrollRef} className="scroll-auto min-h-0 flex-1 overflow-y-auto">
        {run === null ? (
          <nav aria-label="子任务列表" className="flex flex-col gap-a8 p-a12">
            {runs.map((item, index) => (
              <button
                key={`${item.callId}:${item.index}`}
                type="button"
                onClick={() => setSelected(index)}
                title={
                  item.items.length === 0
                    ? '这条子任务的执行明细不落库（刷新或切走会话后只剩任务清单）；结论在主对话那张子智能体卡上'
                    : undefined
                }
                className={cx(
                  'flex items-start gap-a12 rounded-card border-hairline border-hair px-a12 py-a12 text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
                  selected === index && 'bg-overlay-light',
                )}
              >
                <span className="mt-[1px] shrink-0 text-accent">
                  <Icon name="bot" size={15} />
                </span>
                <span className="flex min-w-0 flex-1 flex-col gap-a2">
                  <span className="flex items-center gap-a6">
                    <span className="min-w-0 flex-1 truncate font-ui text-ui text-ink">{item.task}</span>
                    <StatusMark running={item.running && live} />
                  </span>
                  {item.items.length > 0 && (
                    <span className="font-ui text-hint text-ink-muted">{`${stepCount(item)} 步`}</span>
                  )}
                </span>
              </button>
            ))}
          </nav>
        ) : run.items.length === 0 ? (
          <p
            className="p-a12 font-ui text-hint text-ink-muted"
            title="子运行的执行明细不落库；结论在主对话那张子智能体卡上"
          >
            没有可显示的明细
          </p>
        ) : (
          // Same renderer as the main conversation, minus turn folding (sub-runs never settle).
          <div className="px-a12 py-a12">
            <Timeline testId="timeline-subagent" items={run.items} workspaceRoot={workspaceRoot} liveTail />
          </div>
        )}
      </div>
    </div>
  )
}
