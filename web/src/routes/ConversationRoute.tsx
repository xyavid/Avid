import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import {
  queryKeys,
  useAnswerApproval,
  useCancelRun,
  useCreateBranch,
  useEntries,
  useMeta,
  useSession,
  useStartRun,
} from '../api/queries'
import type { PermissionMode } from '../api/types'
import { buildStartRunInput, resolvePermissionMode } from '../features/composer'
import { findToolText, flattenEntries } from '../features/conversation'
import type { InspectorSelection } from '../features/inspector'
import { useTranslation } from '../lib/i18n'
import { runStoreActions, useRunView } from '../state/runStore'
import { useUiStore } from '../state/uiStore'
import type { TimelineEntry, ToolRun } from '../lib/timeline'
import { ConversationSurface } from './ConversationSurface'
import { RunStreamProvider } from './useRunStream'

/** 服务端把 main 作为隐式默认返回（新建会话还没有任何分支值）。 */
const DEFAULT_BRANCH = 'main'

/**
 * L4：会话工作面。数据与副作用都在这里，摆法交给 `ConversationSurface`。
 *
 * 本轮拆开之前这个文件是 307 行：上半是接线、下半是一个 22 字段 props 的装配组件，
 * 中间隔着一大段 props 声明。两者关心的东西不同——这里关心「数据从哪来、什么时候复位、
 * 哪些动作发什么请求」，surface 关心「谁在哪个槽里、忙的时候哪个入口禁用」。
 */
export function ConversationRoute() {
  const { t } = useTranslation()
  const { sessionId = null } = useParams()
  const [branch, setBranch] = useState(DEFAULT_BRANCH)
  const entries = useEntries(sessionId, branch)
  const session = useSession(sessionId)
  // 沙箱后端是**服务端事实**（本机能不能真套沙箱），不是模式的推论：读 meta 的
  // capabilities.sandbox，界面才能说出"工作区"与"不可用"的差别。
  const meta = useMeta()
  const startRun = useStartRun()
  const cancelRun = useCancelRun()
  const answer = useAnswerApproval()
  const createBranch = useCreateBranch()
  const queryClient = useQueryClient()
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
  const flat = useMemo(() => flattenEntries(entries.data?.pages), [entries.data])
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
    // 运行结束会在会话里写一次用量快照，而用量是从分支查询读的：不失效它，
    // 界面上会一直显示上一次运行的读数（活动域被 reset 之后就露馅了）。
    void queryClient.invalidateQueries({
      queryKey: queryKeys.branches(sessionId ?? ''),
    })
  }, [refetchEntries, refetchSession, queryClient, sessionId])

  const inspectTool = useCallback(
    (run: ToolRun) => {
      setSelection({
        title: run.tool || run.toolCallId,
        text: findToolText(view.entries, run.toolCallId),
        arguments: run.arguments,
      })
      setInspectorTab('content')
    },
    [setInspectorTab, view.entries],
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
      <section className="surface-main flex flex-1 flex-col items-center justify-center gap-2 p-8">
        <h1 className="text-xl font-semibold">{t('sessions.title')}</h1>
        {/* 指路，不是「还没有会话」：导航列里可能正列着一堆会话（会话不是导航项，
            这个页面只作为根路径与未知路径的落点）。 */}
        <p className="empty-note max-w-md">{t('sessions.choose')}</p>
      </section>
    )
  }

  return (
    <RunStreamProvider runId={runId} onResync={refresh}>
      <ConversationSurface
        sessionId={sessionId}
        sessionName={session.data?.name ?? null}
        workspaceName={session.data?.workspace?.name ?? null}
        truncatedTail={Boolean(session.data?.truncated_tail)}
        entriesLoading={entries.isLoading}
        branch={branch}
        permission={permissionMode}
        sandbox={meta.data?.capabilities?.sandbox ?? null}
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
