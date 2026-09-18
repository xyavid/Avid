import { useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { useTranslation } from '../../../lib/i18n'

import type { RunView, TimelineEntry, ToolRun } from '../../../events/reducer'
import type { Density } from '../../../state/uiStore'
import { ConversationHeader } from './ConversationHeader'
import { ProcessingCard } from './ProcessingCard'
import { StatusBanner } from './StatusBanner'
import { Timeline } from './Timeline'

export interface ConversationViewProps {
  sessionId: string | null
  sessionName: string | null
  truncatedTail: boolean
  view: RunView
  density: Density
  inspectorOpen: boolean
  loading: boolean
  degraded: boolean
  reconnectAttempt: number | null
  onInspect: (entry: TimelineEntry) => void
  onInspectTool: (run: ToolRun) => void
  onToggleInspector: () => void
  onRefetch: () => void
  /** 审批队列与输入条由 route 组合进来：feature 之间不得互相 import（§3.4）。 */
  approvalsSlot?: ReactNode
  composerSlot?: ReactNode
}

const BUSY_PHASES = new Set(['submitting', 'streaming', 'awaiting_approval', 'cancelling'])

/** 主表面：对话卡。头部 + 提示条 + 审批队列 + 时间线 + 处理中卡片 + 输入条。 */
export function ConversationView(props: ConversationViewProps) {
  const { t } = useTranslation()
  const approvalsRef = useRef<HTMLDivElement | null>(null)
  const [jumpToken, setJumpToken] = useState(0)
  const { view } = props
  const busy = BUSY_PHASES.has(view.phase)
  const pending = view.approvals.filter((item) => item.decision === null)
  const activeTool = view.tools.find((tool) => tool.status === 'running')?.tool ?? null
  const lastCompaction = view.compactions[view.compactions.length - 1]
  const memoryRatio =
    lastCompaction && lastCompaction.before > 0
      ? lastCompaction.after / lastCompaction.before
      : null

  return (
    <section className="sketch-main flex h-full min-h-0 flex-col overflow-hidden">
      <ConversationHeader
        sessionName={props.sessionName}
        sessionId={props.sessionId}
        runId={view.runId}
        phase={view.phase}
        round={view.round}
        tokens={view.tokens}
        pendingApprovals={pending.length}
        memoryRatio={memoryRatio}
        inspectorOpen={props.inspectorOpen}
        onToggleInspector={props.onToggleInspector}
        onFocusApprovals={() =>
          approvalsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
        }
        onScrollToBottom={() => setJumpToken((token) => token + 1)}
      />

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
        <div ref={approvalsRef} className="px-3 pt-2">
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
        onCopy={(text) => void navigator.clipboard?.writeText(text)}
        resetKey={props.sessionId ?? 'none'}
        jumpToken={jumpToken}
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
