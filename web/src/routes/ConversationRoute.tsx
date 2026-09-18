import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { useAnswerApproval, useCancelRun, useEntries, useSession, useStartRun } from '../api/queries'
import type { Entry } from '../api/types'
import { ApprovalQueue } from '../features/approvals'
import { Composer } from '../features/composer'
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

/** L4：会话工作面。唯一把查询结果与活动域拼在一起的地方。 */
export function ConversationRoute() {
  const { t } = useTranslation()
  const { sessionId = null } = useParams()
  const entries = useEntries(sessionId)
  const session = useSession(sessionId)
  const startRun = useStartRun()
  const cancelRun = useCancelRun()
  const answer = useAnswerApproval()
  const view = useRunView()

  const density = useUiStore((state) => state.density)
  const inspectorOpen = useUiStore((state) => state.inspectorOpen)
  const inspectorTab = useUiStore((state) => state.inspectorTab)
  const setInspectorTab = useUiStore((state) => state.setInspectorTab)
  const toggleInspector = useUiStore((state) => state.toggleInspector)
  const autoApprove = useUiStore((state) => state.autoApprove)

  const [startedRunId, setStartedRunId] = useState<string | null>(null)
  const [selection, setSelection] = useState<InspectorSelection | null>(null)

  const runId = startedRunId ?? session.data?.active_run_id ?? null
  const refetchEntries = entries.refetch
  const refetchSession = session.refetch

  // 切换会话：活动域清空、胶带与选择一起换。
  useEffect(() => {
    runStoreActions.reset(sessionId)
    setStartedRunId(null)
    setSelection(null)
  }, [sessionId])

  // 权威视图来自条目：首屏、刷新、resync、断线对账都走这一条。
  const flat = useMemo(() => flatten(entries.data?.pages), [entries.data])
  useEffect(() => {
    if (flat.length > 0) runStoreActions.rebuild(flat)
  }, [flat])

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
        truncatedTail={Boolean(session.data?.truncated_tail)}
        entriesLoading={entries.isLoading}
        onSend={(prompt) => {
          startRun.mutate(
            { sessionId, run: { prompt, auto_approve: autoApprove } },
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
        onToggleInspector={() => toggleInspector()}
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
  truncatedTail: boolean
  entriesLoading: boolean
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
  onToggleInspector: () => void
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
          truncatedTail={props.truncatedTail}
          view={view}
          density={props.density}
          inspectorOpen={props.inspectorOpen}
          loading={props.entriesLoading}
          degraded={degraded}
          reconnectAttempt={reconnectAttempt}
          onInspect={props.onInspect}
          onInspectTool={props.onInspectTool}
          onToggleInspector={props.onToggleInspector}
          onRefetch={refresh}
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
