/**
 * 对话主页面（本次重构的**唯一页面**）。
 *
 * 组装顺序即视觉顺序：状态条 → 时间线 → 审批条 → 输入区。
 * 这个文件只做组装与转发，不含任何展示细节——每个块都有自己的组件与取舍注释。
 *
 * 一条重要的边界：**`view`（一次运行的流式视图）与 `nav`（导航状态）是两套状态**。
 * 前者每帧都在变，后者是点击级频率。所以本页只订阅前者，导航量从 props 进来；
 * 否则每来一个 token 都会把会话列表带着重渲染一遍。
 */

import { useMemo } from 'react'

import type { Branch, PermissionMode, SandboxState, SessionSummary } from '../api/types'
import { ApprovalBar, Composer } from '../features/composer'
import {
  ConversationHeader,
  StatusBanner,
  Timeline,
  TodoPanel,
  latestTodos,
} from '../features/conversation'
import type { InspectorSelection, InspectorTab } from '../features/inspector'
import type { ToolRun } from '../events/reducer'
import { useRunStream } from '../state/useRunStream'
import { AppShell } from './AppShell'
import { InspectorSlot } from './InspectorSlot'
import { useViewport } from './useViewport'

export interface ConversationPageProps {
  session: SessionSummary | null
  branch: string
  /** 该会话的可用分支；少于 2 条时头部不显示分支选择。 */
  branches: Branch[]
  /** 分支列表拉取失败时的说明；它只影响头部的选择器，不挡主流程。 */
  branchError?: string | null
  permission: PermissionMode
  onPermissionChange: (mode: PermissionMode) => void
  sandbox: SandboxState | null
  navCollapsed: boolean
  inspectorToolCallId: string | null
  inspectorTab: InspectorTab
  onOpenInspector: (toolCallId: string) => void
  onCloseInspector: () => void
  onInspectorTab: (tab: InspectorTab) => void
  onSelectBranch: (branch: string) => void
  /** 空态能否新建会话（缺少工作区时不能——服务端会 400 `workspace_required`）。 */
  canCreateSession: boolean
  onCreateSession: () => void
  /** 会话被别的标签页删掉等情况下的回头动作。 */
  onRefresh: () => void
  nav: React.ReactNode
}

export function ConversationPage(props: ConversationPageProps) {
  const { session, branch } = props
  const { isWide, isRoomy } = useViewport()

  const stream = useRunStream({ sessionId: session?.id ?? null, branch })

  const todos = useMemo(() => latestTodos(stream.view.tools) ?? [], [stream.view.tools])

  /*
   * 检查器的选中项从工具运行里现算，而不是存一份副本：
   * 运行期间同一次工具调用会先有 `running` 再有 `ok`，存副本就会显示过期结果。
   */
  const selection = useMemo<InspectorSelection | null>(() => {
    if (props.inspectorToolCallId === null) return null
    const run = stream.view.tools.find((item) => item.toolCallId === props.inspectorToolCallId)
    return run === undefined ? null : selectionOf(run)
  }, [props.inspectorToolCallId, stream.view.tools])

  const running =
    stream.view.phase === 'running' || stream.view.phase === 'awaiting_approval'

  return (
    <AppShell
      navCollapsed={props.navCollapsed}
      navInline={isRoomy}
      inspectorInline={isWide}
      nav={props.nav}
      inspector={
        <InspectorSlot
          inline={isWide}
          selection={selection}
          tab={props.inspectorTab}
          onTabChange={props.onInspectorTab}
          onClose={props.onCloseInspector}
        />
      }
    >
      {session === null ? (
        <LandingState canCreate={props.canCreateSession} onCreateSession={props.onCreateSession} />
      ) : (
        <>
          <ConversationHeader
            sessionName={session.name ?? '未命名会话'}
            sessionId={session.id}
            branch={branch}
            branches={props.branches}
            onSelectBranch={props.onSelectBranch}
            usage={stream.view.usage}
            round={stream.view.round}
            tokens={stream.view.tokens}
          />

          <StatusBanner
            phase={stream.view.phase}
            activity={stream.view.activity}
            error={stream.view.error}
            detached={stream.view.detached}
            onRetry={props.onRefresh}
          />

          {/* 流式视图这一层的错误（拉条目 / 订阅）与运行自身的失败是两件事，分开显示。
              线宽走 `border-hair`（全边 0.5px，只有上边可见），颜色用 `border-t-*` 覆成警示色。
              **不要**写 `border-t-hair border-hair`：后者也输出全边 width（先出），
              `border-t-hair` 只覆盖上边，结果是**四边整框**而不是一条顶线（实测过）。 */}
          {stream.error !== null ? (
            <p
              role="status"
              className="border-hair border-t-warn bg-warn-bg px-a16 py-a6 text-hint text-warn"
            >
              {stream.error}
            </p>
          ) : null}

          {/* 分支列表拉失败不影响主流程，但要说出来：否则用户会以为"这个会话只有 main"。 */}
          {props.branchError != null ? (
            <p
              role="status"
              className="border-hair border-t-warn bg-warn-bg px-a16 py-a6 text-hint text-warn"
            >
              分支列表读取失败：{props.branchError}
            </p>
          ) : null}

          {/*
            任务清单放在时间线**之前**，而不是之后：`Timeline` 是 `flex-1` 的滚动容器，
            插在它和输入区之间的任何元素都会挤掉消息区的可滚动高度，导致"滚不到底"。
            放在上方还有个好处——任务清单本来就是"接下来要做什么"，比历史更该在第一屏。
          */}
          {todos.length > 0 ? <TodoPanel todos={todos} /> : null}

          <Timeline
            entries={stream.view.entries}
            tools={stream.view.tools}
            streaming={running}
            onInspect={props.onOpenInspector}
          />

          <ApprovalBar
            approvals={stream.view.approvals}
            onAnswer={stream.answer}
            busy={stream.submitting}
          />

          <Composer
            disabled={false}
            running={running}
            phase={stream.view.phase}
            permission={props.permission}
            onPermissionChange={props.onPermissionChange}
            sandbox={props.sandbox}
            usage={stream.view.usage}
            onSubmit={async (input) => {
              await stream.submit({ prompt: input.prompt, permission: input.mode })
            }}
            onCancel={() => {
              void stream.cancel()
            }}
            onRetry={() => {
              void stream.reload()
            }}
          />
        </>
      )}
    </AppShell>
  )
}

/**
 * `ToolRun` → 检查器选中项。
 *
 * `diff` 只在这个工具的参数形状**确实**带前后文本时才给（edit 类工具的
 * `old_string`/`new_string`）。这里不做猜测式的字段嗅探——给一个错的 diff
 * 比不给更糟，用户会以为那是本次改动的真实内容。
 */
function selectionOf(run: ToolRun): InspectorSelection {
  const before = readText(run.args, ['old_string', 'old_text', 'before'])
  const after = readText(run.args, ['new_string', 'new_text', 'after', 'content'])
  return {
    title: run.tool,
    value: run.args,
    content: run.resultText,
    ...(before !== null && after !== null ? { diff: { before, after } } : {}),
  }
}

function readText(source: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'string') return value
  }
  return null
}

/**
 * 无会话时的落点。
 *
 * 它**不是**"欢迎页"：本次范围不含引导流程，这里只回答一个问题——"我现在该做什么"。
 * 所以它只有一个明确的动作（新建会话），以及一句说明服务端要求（必须先有工作区）。
 *
 * `canCreate` 为 false 时按钮**禁用并说明原因**，而不是让它发一个注定 400
 * `workspace_required` 的请求——把一个可预防的失败做成一次往返，用户只会看到一条错误。
 */
function LandingState({ canCreate, onCreateSession }: { canCreate: boolean; onCreateSession: () => void }) {
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center gap-a16 px-a24 text-center">
      <p className="text-title text-ink">还没有会话</p>
      <p className="max-w-[42ch] text-ui text-ink-muted">
        会话记录一次完整的运行。新建之后，你的每一句话、每次工具调用与审批都会落在这条链上。
      </p>
      <button
        type="button"
        onClick={onCreateSession}
        disabled={!canCreate}
        className="rounded-sm border-thin border-accent bg-accent px-a16 py-a8 text-caption text-card transition-colors duration-fast ease-standard hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-45"
      >
        新建会话
      </button>
      {!canCreate ? (
        <p className="text-hint text-ink-muted">
          还没有可用工作区：先在终端执行 avid workspace add &lt;路径&gt;，再回来新建会话。
        </p>
      ) : null}
    </div>
  )
}
