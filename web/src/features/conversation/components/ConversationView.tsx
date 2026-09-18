import type { ReactNode } from 'react'

import { useTranslation } from '../../../lib/i18n'

import type { Density } from '../../../lib/density'
import type { TimelineEntry, ToolRun } from '../../../lib/timeline'
import type { RunView } from '../../../events/reducer'
import { ConversationHeader } from './ConversationHeader'
import { ProcessingCard } from './ProcessingCard'
import { StatusBanner } from './StatusBanner'
import { Timeline } from './Timeline'

export interface ConversationViewProps {
  sessionId: string | null
  sessionName: string | null
  /** 归属工作区名（可能为 null）：route 以字符串传入，不引入 features/sessions。 */
  workspaceName?: string | null
  truncatedTail: boolean
  view: RunView
  density: Density
  loading: boolean
  degraded: boolean
  reconnectAttempt: number | null
  onInspect: (entry: TimelineEntry) => void
  onInspectTool: (run: ToolRun) => void
  onFork?: (entry: TimelineEntry) => void
  onRefetch: () => void
  /** 审批队列、输入条与分支选择器由 route 组合进来：feature 之间不得互相 import（§3.4）。 */
  approvalsSlot?: ReactNode
  composerSlot?: ReactNode
  branchSlot?: ReactNode
}

const BUSY_PHASES = new Set(['submitting', 'streaming', 'awaiting_approval', 'cancelling'])

/** 主表面：对话卡。头部 + 提示条 + 审批队列 + 时间线 + 处理中卡片 + 输入条。 */
export function ConversationView(props: ConversationViewProps) {
  const { t } = useTranslation()
  const { view } = props
  const busy = BUSY_PHASES.has(view.phase)
  const activeTool = view.tools.find((tool) => tool.status === 'running')?.tool ?? null

  return (
    <section className="sketch-main flex h-full min-h-0 flex-col overflow-hidden">
      <ConversationHeader
        sessionName={props.sessionName}
        workspaceName={props.workspaceName ?? null}
        sessionId={props.sessionId}
        runId={view.runId}
        phase={view.phase}
        round={view.round}
        tokens={view.tokens}
      />

      {props.branchSlot ? <div className="px-3 pt-2">{props.branchSlot}</div> : null}

      <StatusBanner
        phase={view.phase}
        error={view.error}
        degraded={props.degraded}
        reconnectAttempt={props.reconnectAttempt}
        detached={view.detached}
        truncatedTail={props.truncatedTail}
        onRefetch={props.onRefetch}
      />

      {view.approvals.length > 0 && props.approvalsSlot ? (
        <div className="px-3 pt-2">
          {props.approvalsSlot}
        </div>
      ) : null}

      <Timeline
        entries={view.entries}
        tools={view.tools}
        density={props.density}
        loading={props.loading}
        onInspect={props.onInspect}
        onInspectTool={props.onInspectTool}
        onFork={props.onFork}
        onCopy={(text) => void navigator.clipboard?.writeText(text)}
        resetKey={props.sessionId ?? 'none'}
      />

      {busy ? (
        <div className="px-3 pb-2">
          <ProcessingCard
            phaseLabel={t(`chat.status.${view.phase}`)}
            round={view.round}
            tokens={view.tokens}
            activeTool={activeTool}
          />
        </div>
      ) : null}

      <div className="border-t-bold border-ink p-3">{props.composerSlot}</div>
    </section>
  )
}
