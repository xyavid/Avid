/**
 * Conversation surface: session history and the live run's segments merge into one timeline via
 * `mergeItems(history, run)` — history comes from session entries (the authoritative read) and run
 * segments from the event stream; rebuilding history stays limited to first paint, session switch
 * and branch switch, because re-reading entries at settle time would let segments jump position.
 * Layout is owned here (conversation column centered at ≤720px, composer slightly wider via
 * `chat-input`; AppShell only gives the three-column skeleton and scroll bounds), and entries are
 * paged desc, latest 50, reversed locally.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import {
  searchEntries,
  ApiError,
  createBranch,
  createSession,
  createWorkspace,
  deleteSession,
  deleteWorkspace,
  getMeta,
  listBranches,
  listEntries,
  listInputs,
  listSessions,
  listWorkspaces,
  pickFolder,
  renameSession,
} from '../../api/client'
import type { Meta, SessionSummary, UsageReport, WorkspaceSummary } from '../../api/types'
import { ApprovalBar } from '../../components/chat/ApprovalBar'
import { Composer } from '../../components/chat/Composer'
import { Timeline } from '../../components/chat/Timeline'
import type { TimelineItem } from '../../state/timeline'
import {
  itemsFromEntries,
  mergeItems,
  mergePendingInputs,
  subagentRuns,
  timelineSignature,
} from '../../state/timeline'
import { useRunChoice } from '../../state/runModel'
import { useRunStream } from '../../state/useRunStream'
import { useDock } from '../../state/dock'
import { Dock } from '../../components/dock/Dock'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { AvidMark } from '../../ui/Mark'
import { Icon } from '../../ui/Icon'
import { useConversationScroll } from '../../ui/useConversationScroll'
import { SettingsModal } from '../../components/settings/SettingsModal'
import { ProjectCard } from '../../components/session/ProjectCard'
import type { SearchHit } from '../../api/types'
import { SessionNav } from '../../components/session/SessionNav'
import { SidebarFooter } from '../../components/session/SidebarFooter'
import { AppShell } from '../../app/AppShell'

/** Session-action failure text: mostly the server's message, with a few codes replaced by a more
    actionable next step (e.g. `session_busy`). */
function sessionErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'session_busy') return '会话还在运行中——先停止这次运行，再删除'
    return error.message
  }
  return String(error)
}

/** Empty state: mark on the bare paper (no disc behind it) + serif greeting, at most one primary
    action. */
function Welcome({ detail, action }: { detail: string; action?: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-a16">
      <AvidMark size={96} />
      <p className="font-serif text-[20px] tracking-[0.06em] text-ink">有什么可以帮你？</p>
      <p className="font-ui text-hint text-ink-muted">{detail}</p>
      {action}
    </div>
  )
}

export function ConversationPage() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // History: rebuilt on first paint, session switch and branch switch — never at run settle time.
  const [history, setHistory] = useState<TimelineItem[] | null>(null)
  // Branch being viewed: forking switches to the new branch and later sends target it too.
  const [branch, setBranch] = useState('main')
  const [branchHint, setBranchHint] = useState<string | null>(null)
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Load earlier: append via next_cursor, pin the viewport with the anchor (separate from follow).
  const [earlier, setEarlier] = useState<{ hasMore: boolean; cursor: number | null }>({
    hasMore: false,
    cursor: null,
  })
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [pendingAnchor, setPendingAnchor] = useState<{ top: number; height: number } | null>(null)
  const holdFollowRef = useRef(false)
  const anchorRef = useRef<{ top: number; height: number } | null>(null)
  // Full-access (default off): skips destructive confirmation + sandbox, sent as full_access_ack.
  const [full, setFull] = useState(false)
  // Model/effort chosen in the composer and remembered; candidates come from Settings → Models.
  const run = useRunChoice(meta?.capabilities.models ?? [])
  // Workspace candidates and the workspace a new session will use; adding goes through the picker.
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[] | null>(null)
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  const [wsBusy, setWsBusy] = useState(false)
  const [wsHint, setWsHint] = useState<string | null>(null)
  // Session actions (create/rename/delete): one hint line reports the result or failure.
  const [sessionsBusy, setSessionsBusy] = useState(false)
  const [sessionsHint, setSessionsHint] = useState<string | null>(null)
  // Content search: debounced index query; a lagging index is fine, so only found hits are shown.
  const [navQuery, setNavQuery] = useState('')
  const [contentHits, setContentHits] = useState<SearchHit[]>([])
  const [searchingContent, setSearchingContent] = useState(false)
  // Content-search notice: a failure and a lagging index are both stated.
  const [searchNotice, setSearchNotice] = useState<string | null>(null)
  // Jump and highlight are two states: jump is a one-shot paging request, focusEntryId survives.
  const [jump, setJump] = useState<{ sessionId: string; seq: number } | null>(null)
  const [focusEntryId, setFocusEntryId] = useState<string | null>(null)
  // Live run segments belong to live.attachedSession; attachedRunRef prevents re-attaching.
  // `?settings=1` is a dev-only deep link that opens the settings modal.
  const [settingsOpen, setSettingsOpen] = useState(
    () => new URLSearchParams(window.location.search).has('settings'),
  )
  /** Select a session (sidebar or content hit): clear jump and highlight before switching. */
  const selectSession = (id: string) => {
    setJump(null)
    setFocusEntryId(null)
    setSelectedId(id)
    // A previous error must not stick to the next session.
    setError(null)
  }
  const selectedIdRef = useRef<string | null>(null)
  selectedIdRef.current = selectedId
  const branchRef = useRef('main')
  branchRef.current = branch
  const attachedRunRef = useRef<string | null>(null)
  const settledRef = useRef<() => Promise<void>>(async () => {})
  const live = useRunStream(selectedId, () => {
    void settledRef.current()
  })
  settledRef.current = async () => {
    const id = selectedIdRef.current
    if (id) {
      // Settle refetches usage only: re-reading entries would move segments already on the view.
      try {
        const viewed = branchRef.current
        const bl = await listBranches(id)
        const hit = bl.branches.find((b) => b.name === viewed) ?? bl.branches.find((b) => b.is_default)
        setUsage(hit?.usage ?? null)
      } catch {
        // On refetch failure keep the old view; a reload is the fallback.
      }
    }
    try {
      const w = await listSessions()
      setSessions(w.sessions)
    } catch {
      // A failed list refresh must not block settling.
    }
    live.settle()
  }

  // Timeline = history + this run's segments; run segments only show in their own session.
  const runHere = live.attachedSession === selectedId
  const shown: TimelineItem[] =
    history === null ? [] : runHere ? mergeItems(history, live.items) : history
  // Subagent panel data: runs flattened from the whole timeline (history items included).
  const subagents = subagentRuns(shown)
  const dock = useDock()
  const scroll = useConversationScroll(
    history === null ? 'loading' : timelineSignature(shown),
    holdFollowRef,
  )

  useEffect(() => {
    let alive = true
    Promise.all([getMeta(), listSessions()])
      .then(([m, s]) => {
        if (!alive) return
        setMeta(m)
        setSessions(s.sessions)
        if (s.sessions.length > 0) setSelectedId((cur) => cur ?? s.sessions[0]!.id)
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof ApiError ? e.message : String(e))
      })
    listWorkspaces()
      .then((w) => {
        if (alive) setWorkspaces(w.workspaces)
      })
      .catch(() => {
        if (alive) setWorkspaces([])
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    const needle = navQuery.trim()
    if (needle.length < 2) {
      setContentHits([])
      setSearchingContent(false)
      setSearchNotice(null)
      return
    }
    let alive = true
    setSearchingContent(true)
    const timer = window.setTimeout(() => {
      searchEntries(needle, { workspace: activeWorkspaceId, limit: 8 })
        .then((result) => {
          if (!alive) return
          setContentHits(result.hits)
          setSearchNotice(result.behind > 0 ? `索引还落后 ${result.behind} 个会话` : null)
        })
        .catch((e: unknown) => {
          if (!alive) return
          setContentHits([])
          setSearchNotice(e instanceof ApiError ? e.message : '内容检索暂时用不了')
        })
        .finally(() => {
          if (alive) setSearchingContent(false)
        })
    }, 250)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [navQuery, activeWorkspaceId])

  useEffect(() => {
    if (!selectedId) return
    let alive = true
    setHistory(null)
    // Jump from a hit: cursor is exclusive, so seq + 1 starts paging at that entry.
    const anchor = jump?.sessionId === selectedId ? jump.seq + 1 : undefined
    listEntries(selectedId, { branch, limit: 50, cursorSeq: anchor })
      .then(async (page) => {
        if (!alive) return
        const items = itemsFromEntries([...page.entries].reverse())
        setHistory(items)
        setEarlier({ hasMore: page.has_more, cursor: page.next_cursor })
        setError(null)  // this read succeeded, so clear the previous error screen
        // Pending inputs: the queue lives server-side, so queued items survive a reload.
        const queued = await listInputs(selectedId).catch(() => [])
        if (alive && queued.length > 0) {
          setHistory((cur) => mergePendingInputs(cur ?? items, queued))
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setHistory([])
          setError(e instanceof ApiError ? e.message : String(e))
        }
      })
    // Usage and entries are two endpoints; a failed usage snapshot must not break the conversation.
    listBranches(selectedId)
      .then((bl) => {
        if (!alive) return
        const hit = bl.branches.find((b) => b.name === branch) ?? bl.branches.find((b) => b.is_default)
        setUsage(hit?.usage ?? null)
      })
      .catch(() => {
        if (alive) setUsage(null)
      })
    return () => {
      alive = false
    }
     // jump is a dep: a hit click changes it and this pass starts paging from that anchor
  }, [selectedId, branch, jump])

  const selected = sessions?.find((s) => s.id === selectedId) ?? null

  // Switching sessions returns to main: a branch belongs inside one session.
  useEffect(() => {
    setBranch('main')
    setBranchHint(null)
  }, [selectedId])

  // Switching sessions resets the pinned-to-bottom state for the next one.
  useEffect(() => {
    scroll.reset()
  }, [selectedId])

  /** Fork from a message: create the branch and switch to it; "back to main" is the way out. */
  const branchFrom = async (entryId: string) => {
    if (!selectedId) return
    setBranchHint(null)
    try {
      const created = await createBranch(selectedId, { at: entryId })
      setBranch(created.name)
      setBranchHint(`已从这条消息分叉到「${created.name}」；之后的发送都落在这个分支上`)
    } catch (e) {
      setBranchHint(e instanceof ApiError ? e.message : String(e))
    }
  }

  /** Append older history, pinning the viewport to the anchor; a failure keeps the current view. */
  const loadEarlier = async () => {
    if (!selectedId || loadingEarlier || !earlier.hasMore || earlier.cursor === null) return
    setLoadingEarlier(true)
    try {
      const page = await listEntries(selectedId, { branch, limit: 50, cursorSeq: earlier.cursor })
      const el = scroll.ref.current
      anchorRef.current = el ? { top: el.scrollTop, height: el.scrollHeight } : null
      holdFollowRef.current = true
      setHistory((cur) => [...itemsFromEntries([...page.entries].reverse()), ...(cur ?? [])])
      setEarlier({ hasMore: page.has_more, cursor: page.next_cursor })
    } catch {
      // A failed append must not break the conversation view; the button stays clickable.
      holdFollowRef.current = false
      anchorRef.current = null
    } finally {
      setLoadingEarlier(false)
    }
  }

  // The jumped-to hit: scroll it into view once after it renders (layout effect).
  useLayoutEffect(() => {
    if (!focusEntryId) return
    const el = document.querySelector(`[data-entry="${focusEntryId}"]`)
    el?.scrollIntoView({ block: 'center' })
  }, [focusEntryId, history])

  // After prepending, restore the scroll offset from the anchor before paint (layout effect).
  useLayoutEffect(() => {
    if (pendingAnchor === null) return
    const el = scroll.ref.current
    if (el !== null) el.scrollTop = pendingAnchor.top + (el.scrollHeight - pendingAnchor.height)
    setPendingAnchor(null)
    holdFollowRef.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only fires for a new anchor
  }, [pendingAnchor])

  // Default workspace: the selected session's own, else the first candidate; a manual pick wins.
  useEffect(() => {
    if (activeWorkspaceId || !workspaces) return
    const sw = selected?.workspace?.id
    const fallback = sw && workspaces.some((w) => w.id === sw) ? sw : (workspaces[0]?.id ?? null)
    if (fallback) setActiveWorkspaceId(fallback)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fill only while unset
  }, [workspaces, selected?.workspace?.id, activeWorkspaceId])

  // Sessions are managed per project: when the selection is outside the active project, switch.
  useEffect(() => {
    if (!activeWorkspaceId || !sessions) return
    const inProject = sessions.filter((s) => s.workspace?.id === activeWorkspaceId)
    if (selectedId && inProject.some((s) => s.id === selectedId)) return
    setSelectedId(inProject[0]?.id ?? null)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- react to project switches / refreshes
  }, [activeWorkspaceId, sessions])

  // A selected session with a live run (refresh / switch back): attach and replay its stream.
  useEffect(() => {
    const active = selected?.active_run_id ?? null
    if (!active || live.phase !== 'idle' || attachedRunRef.current === active) return
    attachedRunRef.current = active
    live.attach(active)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- attach on active-run changes only
  }, [selected?.active_run_id, live.phase])

  const refreshWorkspaces = async (): Promise<WorkspaceSummary[]> => {
    const w = await listWorkspaces()
    setWorkspaces(w.workspaces)
    return w.workspaces
  }

  const addByPath = async (path: string) => {
    setWsBusy(true)
    setWsHint(null)
    try {
      await createWorkspace({ path })
      const list = await refreshWorkspaces()
      const created = list.find((w) => w.root === path)
      if (created) setActiveWorkspaceId(created.id)
      setWsHint(`已新增工作区：${created?.name ?? path}`)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'workspace_exists') {
        // 409: the directory is already registered — select the existing entry instead of erroring.
        const list = await refreshWorkspaces()
        const detailId = typeof e.detail?.id === 'string' ? e.detail.id : null
        const known = (detailId && list.find((w) => w.id === detailId)) || list.find((w) => w.root === path)
        if (known) setActiveWorkspaceId(known.id)
        setWsHint('该目录已在列表中，已为你选中')
      } else {
        setWsHint(e instanceof ApiError ? e.message : String(e))
      }
    } finally {
      setWsBusy(false)
    }
  }

  /** Remove from the project list: a registry entry, not the session files on disk. */
  const removeWorkspace = async (id: string) => {
    setWsBusy(true)
    setWsHint(null)
    try {
      await deleteWorkspace(id)
      const list = await refreshWorkspaces()
      if (activeWorkspaceId === id) setActiveWorkspaceId(list[0]?.id ?? null)
      setWsHint('已从项目列表移除；会话文件仍在磁盘上，重新登记该目录即可找回')
    } catch (e) {
      setWsHint(e instanceof ApiError ? e.message : String(e))
    } finally {
      setWsBusy(false)
    }
  }

  const addByPicker = async () => {
    setWsBusy(true)
    setWsHint(null)
    try {
      const { path } = await pickFolder()
      if (!path) {
        setWsHint('已取消选择')
        return
      }
      await addByPath(path)
    } catch (e) {
      setWsHint(e instanceof ApiError ? e.message : String(e))
    } finally {
      setWsBusy(false)
    }
  }

  const refreshSessions = async (): Promise<SessionSummary[]> => {
    const list = await listSessions()
    setSessions(list.sessions)
    return list.sessions
  }

  /** Create a session in the current project and select it (the server requires a workspace). */
  const createSessionInProject = async () => {
    if (!activeWorkspaceId || sessionsBusy) return
    setSessionsBusy(true)
    setSessionsHint(null)
    try {
      const created = await createSession({ workspace: activeWorkspaceId })
      await refreshSessions()
      setSelectedId(created.id)
    } catch (e) {
      setSessionsHint(sessionErrorText(e))
    } finally {
      setSessionsBusy(false)
    }
  }

  const renameSessionById = async (id: string, name: string) => {
    setSessionsBusy(true)
    setSessionsHint(null)
    try {
      await renameSession(id, name)
      await refreshSessions()
    } catch (e) {
      setSessionsHint(sessionErrorText(e))
    } finally {
      setSessionsBusy(false)
    }
  }

  /** Delete a session (already confirmed); if selected, fall back to the first in project. */
  const deleteSessionById = async (id: string) => {
    setSessionsBusy(true)
    setSessionsHint(null)
    try {
      await deleteSession(id)
      // Drop its hits now: the index clears them on the next notify/scan and stale clicks 404.
      setContentHits((current) => current.filter((hit) => hit.session_id !== id))
      const list = await refreshSessions()
      if (selectedIdRef.current === id) {
        const inProject = list.filter((s) => s.workspace?.id === activeWorkspaceId)
        setSelectedId(inProject[0]?.id ?? null)
      }
      setSessionsHint('会话已删除（磁盘上的记录文件一并销毁）')
    } catch (e) {
      setSessionsHint(sessionErrorText(e))
    } finally {
      setSessionsBusy(false)
    }
  }

  // Context-ring reading: live per-turn snapshots while running, else the persisted branch value.
  const shownUsage = runHere ? (live.usage ?? usage) : usage
  const liveActive = live.phase === 'starting' || live.phase === 'running' || live.phase === 'settling'
  const liveHere = liveActive && runHere
  // Only load failures own the body; a failed run is already an error segment in the timeline.
  const displayError = error ?? (live.phase === 'error' && runHere ? live.error : null)

  let body = (
    <Welcome
      detail={activeWorkspaceId ? '这个项目还没有会话' : '从左侧选择一个项目'}
      action={
        activeWorkspaceId ? (
          <Button variant="primary" onClick={() => void createSessionInProject()} disabled={sessionsBusy}>
            新建会话
          </Button>
        ) : undefined
      }
    />
  )
  if (displayError) {
    body = <p className="px-a8 pt-a8 font-ui text-ui text-danger">{displayError}</p>
  } else if (selectedId && history === null) {
    body = <p className="px-a8 font-ui text-hint text-ink-muted">加载中…</p>
  } else if (selectedId) {
    const hasItems = shown.length > 0
    body = (
      <>
        {selected?.truncated_tail && (
          <p className="mb-a16 text-center font-ui text-hint text-ink-muted">上次运行在此中断</p>
        )}
        {earlier.hasMore && hasItems && (
          <div className="mb-a8 flex justify-center">
            <button
              type="button"
              onClick={() => void loadEarlier()}
              disabled={loadingEarlier}
              className="rounded-sm border-hairline border-hair px-a10 py-a4 font-ui text-hint text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light disabled:opacity-40"
            >
              {loadingEarlier ? '加载中…' : '加载更早'}
            </button>
          </div>
        )}
        {/* History and the live run share one timeline; finished turns fold into a single row. */}
        {hasItems && (
          <Timeline
            items={shown}
            sessionId={selectedId}
            workspaceRoot={selected?.workspace?.root ?? null}
            liveTail={liveHere}
            onBranch={(id) => void branchFrom(id)}
            onOpenSubagents={() => dock.select('subagents')}
            onDropInput={(inputId) => void live.dropInput(inputId)}
            focusEntry={focusEntryId}
          />
        )}
        {!hasItems && !liveHere && <Welcome detail="这个会话还没有对话内容" />}
      </>
    )
  }

  return (
    <>
      <AppShell
      actions={
        <IconButton
          icon="panel-right"
          label="侧边栏"
          aria-pressed={dock.open}
          onClick={dock.toggle}
        />
      }
      // Collapsed rail = not mounted (grid back to two columns); an open starts on the chooser.
      rail={
        dock.open ? (
          <Dock
            active={dock.active}
            choosing={dock.choosing}
            onChoose={dock.choose}
            onSelect={dock.select}
            onClose={dock.close}
            workspaceRoot={selected?.workspace?.root ?? null}
            workspaceId={selected?.workspace?.id ?? null}
            subagentRuns={subagents}
            live={liveHere}
            sourceSessionId={selectedId}
            model={run.model}
            effort={run.effort}
          />
        ) : undefined
      }
      sidebar={
        <div className="flex min-h-0 flex-1 flex-col gap-a12">
          <ProjectCard
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            sessionWorkspaceId={selected?.workspace?.id ?? null}
            pickerAvailable={meta?.capabilities.workspace_picker != null}
            busy={wsBusy}
            hint={wsHint}
            onSelectWorkspace={setActiveWorkspaceId}
            onAddByPicker={addByPicker}
            onDeleteWorkspace={(id) => void removeWorkspace(id)}
          />
          <SessionNav
            sessions={sessions ?? []}
            workspaceId={activeWorkspaceId}
            selectedId={selectedId}
            onSelect={selectSession}
            onCreateSession={() => void createSessionInProject()}
            onRenameSession={(id, name) => void renameSessionById(id, name)}
            onDeleteSession={(id) => void deleteSessionById(id)}
            contentHits={contentHits}
            contentSearching={searchingContent}
            searchNotice={searchNotice}
            onSearchQuery={setNavQuery}
            onSelectHit={(hit) => {
              selectSession(hit.session_id)
              setJump({ sessionId: hit.session_id, seq: hit.seq })
              setFocusEntryId(hit.entry_id)
            }}
            creating={sessionsBusy}
            notice={sessionsHint}
          />
          {/* Settings lives at the bottom of the sidebar as its own column. */}
          <SidebarFooter onOpenSettings={() => setSettingsOpen(true)} />
        </div>
      }
      main={
        <div className="mx-auto flex h-full max-w-chat-input flex-col">
          <div className="relative min-h-0 flex-1">
            <div
              ref={scroll.ref}
              onScroll={scroll.handleScroll}
              className="scroll-auto flex h-full flex-col overflow-y-auto px-a16 pt-a16"
            >
              {body}
            </div>
            {!scroll.pinned && (
              <button
                type="button"
                onClick={scroll.scrollToBottom}
                aria-label="跳到底部"
                className="absolute bottom-a8 left-1/2 flex h-[28px] -translate-x-1/2 items-center gap-a4 rounded-full border-hairline border-hair bg-card px-a12 font-ui text-hint text-ink shadow-soft transition-colors duration-fast ease-out hover:bg-overlay-light"
              >
                <Icon name="chevron-down" size={12} />
                最新
              </button>
            )}
          </div>
          {(branch !== 'main' || branchHint) && (
            <div className="mx-auto flex w-full max-w-chat-input items-center justify-between gap-a8 border-t border-hair px-a16 py-a6">
              <span className="min-w-0 truncate font-ui text-hint text-ink-muted">
                {branchHint ?? `分支：${branch}`}
              </span>
              {branch !== 'main' && (
                <button
                  type="button"
                  onClick={() => {
                    setBranch('main')
                    setBranchHint(null)
                  }}
                  className="shrink-0 rounded-sm px-a8 py-[2px] font-ui text-hint text-accent transition-colors duration-fast ease-out hover:bg-overlay-light"
                >
                  回到主线
                </button>
              )}
            </div>
          )}
          {live.approvals.length > 0 && runHere && (
            <div className="px-a16 pb-a8">
              <ApprovalBar
                approvals={live.approvals}
                busy={false}
                onDecide={(id, decision) => void live.decide(id, decision)}
                onAnswer={(id, text) => void live.answer(id, text)}
              />
            </div>
          )}
          <Composer
            full={full}
            onToggleFull={setFull}
            disabled={!selectedId}
            busy={liveHere}
            model={run.model}
            onChangeModel={run.chooseModel}
            effort={run.effort}
            onChangeEffort={run.chooseEffort}
            byokModels={meta?.capabilities.models ?? []}
            usage={shownUsage}
            onSend={(text, images) => void live.send(text, full, run.model, branch, run.effort, images)}
            onQueue={(text, images) => void live.submit(text, 'after', images)}
            onInsert={(text, images) => void live.submit(text, 'now', images)}
            onStop={() => void live.stop()}
          />
        </div>
      }
      />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </>
  )
}
