import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import {
  useAnswerApproval,
  useCancelRun,
  useCreateBranch,
  useEntries,
  useSession,
  useStartRun,
} from '../api/queries'
import type { Entry, PermissionMode } from '../api/types'
import { ApprovalQueue } from '../features/approvals'
import { BranchSelector } from '../features/branches'
import { buildStartRunInput, Composer, resolvePermissionMode } from '../features/composer'
import { ConversationView } from '../features/conversation'
import type { InspectorSelection } from '../features/inspector'
import { Inspector } from '../features/inspector'
import { InspectorSlot } from '../layouts/InspectorSlot'
import { useTranslation } from '../lib/i18n'
import { runStoreActions, useRunView } from '../state/runStore'
import { useUiStore } from '../state/uiStore'
import type { TimelineEntry, ToolRun } from '../events/reducer'
import { RunStreamProvider, useRunStreamState } from './useRunStream'

function flatten(pages: { entries: Entry[] }[] | undefined): Entry[] {
  if (!pages) return []
  return pages.flatMap((page) => page.entries)
}

/** 服务端把 main 作为隐式默认返回（新建会话还没有任何分支值）。 */
const DEFAULT_BRANCH = 'main'

/** L4：会话工作面。唯一把查询结果与活动域拼在一起的地方。 */
export function ConversationRoute() {
  const { t } = useTranslation()
  const { sessionId = null } = useParams()
  const [branch, setBranch] = useState(DEFAULT_BRANCH)
  const entries = useEntries(sessionId, branch)
  const session = useSession(sessionId)
  const startRun = useStartRun()
  const cancelRun = useCancelRun()
  const answer = useAnswerApproval()
  const createBranch = useCreateBranch()
  const view = useRunView()

  const density = useUiStore((state) => state.density)
  const inspectorOpen = useUiStore((state) => state.inspectorOpen)
  const inspectorTab = useUiStore((state) => state.inspectorTab)
  const setInspectorTab = useUiStore((state) => state.setInspectorTab)
  const toggleInspector = useUiStore((state) => state.toggleInspector)
  const autoApprove = useUiStore((state) => state.autoApprove)

  const [startedRunId, setStartedRunId] = useState<string | null>(null)
  const [selection, setSelection] = useState<InspectorSelection | null>(null)
  // 权限模式：本次会话视图的瞬时选择（null = 还没选过）。不是偏好，不进 uiStore——
  // 「上次选了 system，下次打开浏览器仍自动全放行」是安全默认值问题。
  const [permission, setPermission] = useState<PermissionMode | null>(null)

  // 缺省取当前会话所属工作区的默认权限，取不到就是 strict（与服务端的回落同值）。
  const permissionMode = resolvePermissionMode(
    permission,
    session.data?.workspace?.default_permission,
  )

  const runId = startedRunId ?? session.data?.active_run_id ?? null
  const refetchEntries = entries.refetch
  const refetchSession = session.refetch

  // 切换会话：活动域清空、胶带与选择一起换，分支回到默认那条，权限重新按新会话
  // 所属工作区回落（上一个会话里选的档不跨会话复用——那是另一个权限边界的决定）。
  useEffect(() => {
    runStoreActions.reset(sessionId)
    setStartedRunId(null)
    setSelection(null)
    setBranch(DEFAULT_BRANCH)
    setPermission(null)
  }, [sessionId])

  // 换分支 = 换历史：活动域清空，等该分支的条目到达后重建。服务端没有「当前分支」
  // 这个概念（它只有一组链尾值），所以这只是本地视图状态。
  const switchBranch = useCallback(
    (name: string) => {
      setBranch(name)
      runStoreActions.reset(sessionId)
      setStartedRunId(null)
      setSelection(null)
    },
    [sessionId],
  )

  // 权威视图来自条目：首屏、刷新、resync、断线对账、换分支都走这一条。
  const flat = useMemo(() => flatten(entries.data?.pages), [entries.data])
  useEffect(() => {
    // 用 entries.data 而不是 flat.length 判断：空分支也要重建，否则会留着上一条链的视图。
    if (!entries.data) return
    runStoreActions.rebuild(flat)
  }, [entries.data, flat])

  // 路由切换后焦点移到主内容（键盘用户不该留在 body 上）。
  useEffect(() => {
    document.getElementById('main')?.focus()
  }, [sessionId])

  const refresh = useCallback(() => {
    void refetchEntries()
    void refetchSession()
  }, [refetchEntries, refetchSession])

  const inspectTool = useCallback(
    (run: ToolRun) => {
      const entry = view.entries.find(
        (item) => item.kind === 'tool' && item.toolCallId === run.toolCallId,
      )
      setSelection({
        title: run.tool || run.toolCallId,
        text: entry?.text ?? '',
        arguments: run.arguments,
      })
      setInspectorTab('content')
    },
    [setInspectorTab, view.entries],
  )

  const inspect = useCallback(
    (entry: TimelineEntry) => {
      const tool = entry.toolCallId
        ? view.tools.find((item) => item.toolCallId === entry.toolCallId)
        : undefined
      setSelection({
        title: tool ? `${tool.tool} · ${entry.kind}` : entry.kind,
        text: entry.text,
        arguments: tool?.arguments,
      })
      setInspectorTab('content')
    },
    [setInspectorTab, view.tools],
  )

  // 在某条目处开新分支：成功后直接切过去看那条链。名字由服务端取（b2、b3…）。
  const forkEntry = useCallback(
    (entry: TimelineEntry) => {
      if (!sessionId || !entry.entryId) return
      createBranch.mutate(
        { sessionId, at: entry.entryId },
        {
          onSuccess: (created) => switchBranch(created.name),
          onError: (error) => console.error('[branch] 分叉失败', error),
        },
      )
    },
    [createBranch, sessionId, switchBranch],
  )

  if (!sessionId) {
    return (
      <section className="sketch-main flex flex-1 flex-col items-center justify-center gap-2 p-8">
        <h1 className="font-sketch text-xl">{t('sessions.title')}</h1>
        <p className="empty-note max-w-md">{t('sessions.empty')}</p>
      </section>
    )
  }

  return (
    <RunStreamProvider runId={runId} onResync={refresh}>
      <ConversationBody
        sessionId={sessionId}
        sessionName={session.data?.name ?? null}
        workspaceName={session.data?.workspace?.name ?? null}
        truncatedTail={Boolean(session.data?.truncated_tail)}
        entriesLoading={entries.isLoading}
        branch={branch}
        permission={permissionMode}
        hasActiveRun={Boolean(session.data?.active_run_id)}
        onSwitchBranch={switchBranch}
        onPermissionChange={setPermission}
        onSend={(prompt) => {
          startRun.mutate(
            {
              sessionId,
              run: buildStartRunInput({ prompt, branch, autoApprove, permission: permissionMode }),
            },
            {
              onSuccess: (created) => {
                runStoreActions.reset(sessionId)
                setStartedRunId(created.run_id)
              },
            },
          )
        }}
        onStop={() => {
          if (!runId) return
          runStoreActions.requestCancel()
          cancelRun.mutate(runId)
        }}
        answering={answer.isPending}
        onAnswer={(approvalId, decision) => {
          if (!runId) return
          answer.mutate({ runId, approvalId, decision })
        }}
        onInspect={inspect}
        onInspectTool={inspectTool}
        onFork={forkEntry}
        inspectorOpen={inspectorOpen}
        inspectorTab={inspectorTab}
        setInspectorTab={setInspectorTab}
        selection={selection}
        onCloseInspector={() => {
          toggleInspector(false)
          setSelection(null)
        }}
        density={density}
      />
    </RunStreamProvider>
  )
}

interface BodyProps {
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
  onInspect: (entry: TimelineEntry) => void
  onInspectTool: (run: ToolRun) => void
  onFork: (entry: TimelineEntry) => void
  onSwitchBranch: (name: string) => void
  onPermissionChange: (mode: PermissionMode) => void
  setInspectorTab: (tab: 'content' | 'diff' | 'json') => void
  onCloseInspector: () => void
}

function ConversationBody(props: BodyProps) {
  const view = useRunView()
  const { degraded, reconnectAttempt, refresh } = useRunStreamState()
  const busy = ['submitting', 'streaming', 'awaiting_approval', 'cancelling'].includes(view.phase)

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
          onInspect={props.onInspect}
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
              onSend={props.onSend}
              onStop={props.onStop}
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
