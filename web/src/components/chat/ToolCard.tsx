/**
 * 工具卡双形态：
 * - **折叠态（默认）**：单行——工具图标 + 动作词 + 目标（相对路径 / 命令 / 任务名，
 *   等宽小字截断）+ 耗时 + 状态标记，点击展开；
 * - **展开态**：完整卡——头部同上，正文是工具结果；subagent 的子步骤在结果之前
 *   按任务分组列出（子步骤是 live-only：子运行不落库，刷新后只剩这张卡与它的结果）。
 *
 * 宽度 330px 是组件墙的解剖值；失败判定与后端同口径，单点在 `state/timeline.ts`
 * 的 `classifyToolResult`（改动要两侧同步）。
 */

import { useState } from 'react'
import type { ReactNode } from 'react'

import type { SubagentStep, ToolStatus } from '../../state/timeline'
import { cx } from '../../ui/cx'
import { durationLabel } from '../../ui/duration'
import { Icon, type IconName } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'
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

/** 子步骤按任务分组：并行 subagent 的步骤会交错到达，按任务聚而不是按到达顺序切。 */
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
  /** 动作词：读取 / 写入 / 执行 / 子智能体…（见 toolLabel）。 */
  verb: string
  /** 目标：相对路径、命令首行、任务名；空 = 这条调用没有可读目标。 */
  target?: string
  status?: ToolStatus
  /** 运行期读数（tool_call_finished 的 duration_ms）；null = 没有读数。 */
  durationMs?: number | null
  /** 子 agent 的子步骤；空数组 = 不是 subagent 卡或还没有步骤。 */
  steps?: SubagentStep[]
  /** 工作区根：子步骤的相对路径也按它算。 */
  workspaceRoot?: string | null
  defaultExpanded?: boolean
  className?: string
  /** 展开态正文：工具结果（没有结果时给参数）。 */
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
  children,
}: ToolCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const duration = durationMs === null ? '' : durationLabel(durationMs)

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
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
        'w-[330px] max-w-full overflow-hidden rounded-card border-hairline border-hair bg-card shadow-soft',
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
      <div className="px-a12 py-[9px] font-mono text-micro leading-[1.6] text-ink-muted">{children}</div>
    </div>
  )
}
