/**
 * L4 的**装配层**：把三个 feature 的组合槽（审批队列 / 输入条 / 分支选择器）、对话卡与
 * 检查器摆到一起，并决定「运行还在忙」时哪些入口禁用。
 *
 * 与 `ConversationRoute` 的分工是一条线：route 负责**数据与副作用**（URL 参数、查询、
 * 切换会话/分支时的活动域复位、发起运行与取消），这里负责**摆法与启用条件**。
 * 拆开的动因是原先两者同在一个 307 行的文件里，并且中间隔着一个 22 字段的 props 中转
 * 对象——route 把一切都算好、再打包交给同文件下方的一个组件，读的时候必须来回跳。
 *
 * 活动域（`useRunView`）与流状态（`useRunStreamState`）在这里**自己读**，不走 props：
 * 它们是 L2 的只读域，route 没有独占权，中转一层只会让 props 表更长。
 * 走 props 的只有 route 独占的东西——URL 参数、查询结果片段、以及它整理好的动作回调。
 */
import { ApprovalQueue } from '../features/approvals'
import { BranchSelector } from '../features/branches'
import { Composer } from '../features/composer'
import { ConversationView, isActivePhase } from '../features/conversation'
import { Inspector } from '../features/inspector'
import type { InspectorSelection } from '../features/inspector'
import { InspectorSlot } from '../layouts/InspectorSlot'
import type { TimelineEntry, ToolRun } from '../lib/timeline'
import type { PermissionMode, SandboxState } from '../api/types'
import { useRunView } from '../state/runStore'
import { useRunStreamState } from './useRunStream'

export interface ConversationSurfaceProps {
  sessionId: string
  sessionName: string | null
  /** 会话归属的工作区名（可能为 null）；route 从会话详情取，feature 之间不互相 import。 */
  workspaceName: string | null
  truncatedTail: boolean
  entriesLoading: boolean
  /** 当前查看的分支（本地视图状态，服务端没有「当前分支」）。 */
  branch: string
  /** 这次运行的权限模式（已按工作区默认回落，不是 null）。 */
  permission: PermissionMode
  /** 服务端上报的沙箱后端（meta 未就绪时为 null）。 */
  sandbox: SandboxState | null
  /** 服务端说这个会话有活动 run：切换与分叉都会失败，先把入口禁掉。 */
  hasActiveRun: boolean
  density: 'compact' | 'comfy'
  inspectorOpen: boolean
  inspectorTab: 'content' | 'diff' | 'json'
  selection: InspectorSelection | null
  onSend: (prompt: string) => void
  onStop: () => void
  onAnswer: (approvalId: string, decision: 'allow' | 'deny') => void
  answering: boolean
  onInspectTool: (run: ToolRun) => void
  onFork: (entry: TimelineEntry) => void
  onSwitchBranch: (name: string) => void
  onPermissionChange: (mode: PermissionMode) => void
  setInspectorTab: (tab: 'content' | 'diff' | 'json') => void
  onCloseInspector: () => void
}

export function ConversationSurface(props: ConversationSurfaceProps) {
  const view = useRunView()
  const { degraded, reconnectAttempt, refresh } = useRunStreamState()
  const busy = isActivePhase(view.phase)

  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <ConversationView
          sessionId={props.sessionId}
          sessionName={props.sessionName}
          workspaceName={props.workspaceName}
          truncatedTail={props.truncatedTail}
          view={view}
          density={props.density}
          loading={props.entriesLoading}
          degraded={degraded}
          reconnectAttempt={reconnectAttempt}
          onInspectTool={props.onInspectTool}
          onFork={props.onFork}
          onRefetch={refresh}
          branchSlot={
            <BranchSelector
              sessionId={props.sessionId}
              current={props.branch}
              disabled={busy || props.hasActiveRun}
              onSwitch={props.onSwitchBranch}
            />
          }
          approvalsSlot={
            <ApprovalQueue
              approvals={view.approvals}
              // 只在本条答复的请求在飞时禁用：运行处于 awaiting_approval 时按钮必须可点。
              busy={props.answering}
              onAnswer={props.onAnswer}
            />
          }
          composerSlot={
            <Composer
              busy={busy}
              canSend={true}
              stopping={view.phase === 'cancelling'}
              permission={props.permission}
              onPermissionChange={props.onPermissionChange}
              sandbox={props.sandbox}
              onSend={props.onSend}
              onStop={props.onStop}
              sessionId={props.sessionId}
              branch={props.branch}
            />
          }
        />
      </div>
      {props.inspectorOpen ? (
        <InspectorSlot>
          <Inspector
            open={props.inspectorOpen}
            tab={props.inspectorTab}
            density={props.density}
            selection={props.selection}
            onTabChange={props.setInspectorTab}
            onClose={props.onCloseInspector}
          />
        </InspectorSlot>
      ) : null}
    </>
  )
}
