/**
 * Tool card: a collapsed single row (icon + verb + target + duration + status) or the full
 * card — detail view for file tools, subagent steps grouped by task, else the raw result.
 * Subagent steps are live-only (child runs are not persisted); the failed verdict shares its
 * rule with the backend via `classifyToolResult` in `state/timeline.ts` — keep both in sync.
 */

import { useState } from 'react'
import type { ReactNode } from 'react'

import { CodeBlock } from '../../markdown'
import type { SubagentStep, ToolStatus } from '../../state/timeline'
import { cx } from '../../ui/cx'
import { durationLabel } from '../../ui/duration'
import { Icon, type IconName } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'
import { DiffView } from './DiffView'
import type { ToolDetail } from './toolDetail'
import { toolLabel } from './toolLabel'

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

/** Group by task: parallel subagent steps interleave, so arrival order would split them. */
function stepGroups(steps: SubagentStep[]): Array<{ task: string; list: SubagentStep[] }> {
  const order: string[] = []
  const byTask = new Map<string, SubagentStep[]>()
  for (const step of steps) {
    let list = byTask.get(step.task)
    if (list === undefined) {
      list = []
      byTask.set(step.task, list)
      order.push(step.task)
    }
    list.push(step)
  }
  return order.map((task) => ({ task, list: byTask.get(task)! }))
}

function StepRow({ step, workspaceRoot }: { step: SubagentStep; workspaceRoot: string | null }) {
  const label = toolLabel(step.name, step.args, workspaceRoot)
  return (
    <div className="flex items-center gap-a6 py-[1px] font-ui text-hint text-ink-muted">
      <span className="shrink-0 text-accent">
        <Icon name={label.icon} size={11} />
      </span>
      <span className="shrink-0 text-ink-light">{label.verb}</span>
      {label.target && <span className="min-w-0 flex-1 truncate">{label.target}</span>}
      <StatusMark status={step.status} />
    </div>
  )
}

export type ToolCardProps = {
  icon: IconName
  /** Action word from toolLabel. */
  verb: string
  /** Relative path, command first line or task name; empty = nothing readable. */
  target?: string
  status?: ToolStatus
  /** Live reading (`tool_call_finished` duration_ms); null = none. */
  durationMs?: number | null
  /** Subagent steps; empty = not a subagent card or no steps yet. */
  steps?: SubagentStep[]
  /** Workspace root; sub-step paths are relativized against it too. */
  workspaceRoot?: string | null
  defaultExpanded?: boolean
  className?: string
  /** Detail view for file tools; absent ⇒ raw `children` result. */
  detail?: ToolDetail | null
  /** Notifies the caller when the card expands (subagent cards open the right column); one
   *  click does both on purpose. */
  onOpen?: () => void
  /** Expanded body: the tool result (arguments when there is no result). */
  children?: ReactNode
}

export function ToolCard({
  icon,
  verb,
  target = '',
  status = 'running',
  durationMs = null,
  steps = [],
  workspaceRoot = null,
  defaultExpanded = false,
  className,
  detail = null,
  onOpen,
  children,
}: ToolCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const duration = durationMs === null ? '' : durationLabel(durationMs)

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => {
          setExpanded(true)
          onOpen?.()
        }}
        className={cx(
          'flex w-[330px] max-w-full items-center gap-a8 rounded-sm border-hairline border-hair bg-card px-a8 py-[5px] text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
          className,
        )}
      >
        <span className="shrink-0 text-accent">
          <Icon name={icon} size={13} />
        </span>
        <span className="shrink-0 font-ui text-ui text-ink">{verb}</span>
        {target ? (
          <span className="min-w-0 flex-1 truncate font-mono text-hint text-ink-muted">{target}</span>
        ) : (
          <span className="flex-1" />
        )}
        {duration && <span className="shrink-0 font-mono text-micro text-ink-muted">{duration}</span>}
        <StatusMark status={status} />
      </button>
    )
  }

  return (
    <div
      className={cx(
        'max-w-full overflow-hidden rounded-card border-hairline border-hair bg-card shadow-soft',
        // Diff / code views need horizontal room: expanded cards with a detail widen to 560px.
        detail === null ? 'w-[330px]' : 'w-[560px]',
        className,
      )}
    >
      <div className="flex items-center gap-a8 border-b border-hairline border-hair px-a12 py-a8 font-ui text-caption text-ink-light">
        <span className="shrink-0 text-accent">
          <Icon name={icon} size={13} />
        </span>
        <span className="shrink-0">{verb}</span>
        {target && <span className="min-w-0 flex-1 truncate font-mono text-hint text-ink-muted">{target}</span>}
        {duration && <span className="ml-auto shrink-0 font-mono text-micro text-ink-muted">{duration}</span>}
        <StatusMark status={status} />
        <IconButton
          icon="chevron-down"
          label="收起"
          iconSize={12}
          onClick={() => setExpanded(false)}
          className={duration || target ? undefined : 'ml-auto'}
        />
      </div>
      {steps.length > 0 && (
        <div className="flex flex-col gap-a6 border-b border-hair px-a12 py-a8">
          {stepGroups(steps).map(({ task, list }) => (
            <div key={task} className="flex flex-col gap-a2">
              <div className="truncate font-ui text-hint text-ink-muted">{task}</div>
              {list.map((step) => (
                <StepRow key={step.callId} step={step} workspaceRoot={workspaceRoot} />
              ))}
            </div>
          ))}
        </div>
      )}
      {detail === null ? (
        <div className="px-a12 py-[9px] font-mono text-micro leading-[1.6] text-ink-muted">{children}</div>
      ) : (
        <div className="px-a12 py-a8">
          {detail.kind === 'diff' ? (
            <DiffView before={detail.before} after={detail.after} lang={detail.lang} />
          ) : (
            <CodeBlock
              lang={detail.lang}
              text={detail.text}
              lineNumbers
              startLine={detail.startLine}
            />
          )}
          {/* When the view is not the result (edit / write), the result stays as a footnote. */}
          {detail.note !== null && <p className="font-ui text-hint text-ink-muted">{detail.note}</p>}
        </div>
      )}
    </div>
  )
}
