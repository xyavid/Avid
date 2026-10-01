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

import { useMemo, useState } from 'react'

import type {
  Branch,
  PermissionMode,
  SandboxState,
  SessionSummary,
  WorkspaceSummary,
} from '../api/types'
import { ApprovalBar, Composer } from '../features/composer'
import {
  ConversationHeader,
  StatusBanner,
  Timeline,
  TodoPanel,
  latestTodos,
} from '../features/conversation'
import type { InspectorSelection, InspectorTab } from '../features/inspector'
import { WorkspaceManager } from '../features/workspace'
import type { ToolRun } from '../events/reducer'
import { useRunStream } from '../state/useRunStream'
import { AppShell } from './AppShell'
import { InspectorSlot } from './InspectorSlot'
import { RightRail } from './RightRail'
import { SurfaceTabs } from './SurfaceTabs'
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
  /** 信息面板要用的模型名（`meta.capabilities.model`，可能为 null）。 */
  model: string | null
  /** 已登记的工作区列表（右栏信息面板与左栏分组都用它）。 */
  workspaces: WorkspaceSummary[]
  onCreateSession: () => void
  /** 会话被别的标签页删掉等情况下的回头动作。 */
  onRefresh: () => void
  nav: React.ReactNode
}

export function ConversationPage(props: ConversationPageProps) {
  const { session, branch } = props
  const { isWide, isRoomy } = useViewport()

  const stream = useRunStream({ sessionId: session?.id ?? null, branch })

  /*
   * 工作区管理弹窗的开关状态**放在本页**，而不是各自组件里。
   *
   * 理由：两个入口（左栏底部 / 右栏信息面板）要打开的是**同一个**弹窗。
   * 若各自持有一个 state，"点左边开、点右边也开"会变成两个独立实例同时挂载，
   * 而 `Dialog` 的 Esc 挂在 document 上——一次 Esc 会把两个都关掉，且没人能说清
   * 哪一次关闭是被谁触发的。状态提升到这里是唯一不会出错的形状。
   */
  const [workspaceManagerOpen, setWorkspaceManagerOpen] = useState(false)

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

  /*
   * 信息面板要的"工作目录"：优先取会话 header 里的归属，再按 id 去注册表里
   * 换出 root。会话的 workspace 里本来就有 root，但注册表的那份是当前事实——
   * 注册表里删掉某项不影响会话归属（服务端语义），所以两处都可能缺，
   * 缺就显示"未归属"，不要拿一个空串冒充路径。
   */
  const workspaceRoot =
    session?.workspace?.root ??
    props.workspaces.find((item) => item.id === session?.workspace?.id)?.root ??
    null

  /** 右栏信息面板的 props：一处算好，inline 与浮层两种形态共用。 */
  const info = {
    session,
    branch,
    model: props.model,
    workspaces: props.workspaces,
    workspaceRoot,
    tokens: stream.view.tokens,
    messageCount: session?.message_count ?? 0,
    onManageWorkspaces: () => setWorkspaceManagerOpen(true),
  }

  return (
    <AppShell
      navCollapsed={props.navCollapsed}
      navInline={isRoomy}
      inspectorInline={isWide}
      nav={props.nav}
      /*
       * 顶部条左端**故意留空**：左栏的 header 已经有一个「收起会话列表」按钮，
       * 这里再放一个会造出两个同名可访问控件（`getByRole('button', {name})` 会命中
       * 两个，读屏也会念两遍）。同一个动作只保留一个入口，位置选在它作用的对象
       * （会话列表）自己的 header 上。
       */
      topbarCenter={
        /*
         * 「聊天 / 频道」两个面：本次范围只做了聊天，频道**保留在标签里但禁用**。
         * 删掉它会让用户以为"这个应用只有聊天"，而留着并说明"暂未开放"才是
         * 对现状的诚实表述（`SurfaceTabs` 的 disabled 分支就是为它准备的）。
         */
        <SurfaceTabs
          aria-label="工作面"
          active="chat"
          onChange={() => undefined}
          tabs={[
            { key: 'chat', label: '聊天' },
            { key: 'channel', label: '频道', disabled: true },
          ]}
        />
      }
      inspector={
        isWide ? (
          <RightRail
            selection={selection}
            tab={props.inspectorTab}
            onTabChange={props.onInspectorTab}
            onCloseInspector={props.onCloseInspector}
            info={info}
          />
        ) : (
          <InspectorSlot
            inline={false}
            selection={selection}
            tab={props.inspectorTab}
            onTabChange={props.onInspectorTab}
            onClose={props.onCloseInspector}
          />
        )
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
              /*
               * `input.fullAck` 直接转给运行时：它只在用户**确认过那个「完全访问」
               * 弹窗**之后才为 true（见 Composer 的实现），而请求体里要不要带
               * `full_access_ack` 由 `buildStartRunInput` 单点决定。
               * 少了这条透传，用户在界面上确认了、请求里却没带凭据 —— 服务端 422，
               * 而错误信息只会说"缺少显式授权"，指不到界面这一层。
               */
              await stream.submit({
                prompt: input.prompt,
                permission: input.mode,
                ...(input.fullAck === true ? { fullAck: true } : {}),
              })
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

      {/*
        弹窗挂在页面根部、**不是**挂在左栏或右栏里：它由两个入口共用
        （左栏底部与右栏信息面板），挂进任何一栏都会让另一栏的入口在
        DOM 上够不着它，也会随那一栏的收起/展开被卸载。
      */}
      <WorkspaceManager
        open={workspaceManagerOpen}
        onClose={() => setWorkspaceManagerOpen(false)}
      />
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
