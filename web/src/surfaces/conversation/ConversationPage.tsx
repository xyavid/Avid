/**
 * 对话表面：会话历史与本次运行的段落在这里汇成**一条时间线**。
 *
 * 显示的是 `mergeItems(history, run)`：历史段落来自会话条目（权威回拉的产物），
 * 运行段落来自事件流（`useRunStream`）。两条来源同形，所以过程中逐段追加、
 * 收尾原样留着——收尾只回拉用量与会话列表，不重建条目（重建会让段落换位置，
 * 那就是「最后才整体呈现」的病根）。历史的重建只发生在首屏、切会话、切分支。
 *
 * 布局职责：对话列 ≤720px 居中、输入列略宽（chat-input），由本表面自己排——
 * AppShell 只提供三栏骨架与滚动边界。数据分页：desc 取最近 50 条后本地反转；
 * truncated_tail（上次运行中断）在流顶给一条提示——派生自投影，不新增字段。
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import {
  ApiError,
  createBranch,
  createSession,
  createWorkspace,
  deleteSession,
  deleteWorkspace,
  getMeta,
  listBranches,
  listEntries,
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
import { itemsFromEntries, mergeItems, subagentRuns, timelineSignature } from '../../state/timeline'
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
import { SessionNav } from '../../components/session/SessionNav'
import { SidebarFooter } from '../../components/session/SidebarFooter'
import { AppShell } from '../../app/AppShell'

/** 会话动作的失败说法：服务端消息多半够用，个别码换成更可执行的下一步。 */
function sessionErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'session_busy') return '会话还在运行中——先停止这次运行，再删除'
    return error.message
  }
  return String(error)
}

/** 欢迎态（报告 §6 底部欢迎态）：标识 + 衬线欢迎语（letter-spacing .06em）。
    标识直接贴在纸面上，不做圆托——图案自带配色，透明底。
    空项目时给一个主行动（新建会话）——每屏 ≤1 个 primary，按 Button 的使用约定。 */
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
  // 会话历史段落（权威回拉的产物）：只在首屏、切会话、切分支时重建——运行结束后
  // 不重建，时间线于是不会在收尾那一刻重排（这是本次改动要治的病）。
  const [history, setHistory] = useState<TimelineItem[] | null>(null)
  // 当前查看的分支：分叉后切到新分支，之后的发送也落在它上面（「回到主线」退回去）。
  const [branch, setBranch] = useState('main')
  const [branchHint, setBranchHint] = useState<string | null>(null)
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 「加载更早」：desc 首页只含最近 50 条，更早历史经 next_cursor 追加；
  // anchor 记录追加前的视口位置，prepend 落地后在 layout effect 里把视口
  // 钉回同一条旧消息——独立于跟随的 dep 效果，避免与流式合帧抢提交（评审 L7）。
  const [earlier, setEarlier] = useState<{ hasMore: boolean; cursor: number | null }>({
    hasMore: false,
    cursor: null,
  })
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [pendingAnchor, setPendingAnchor] = useState<{ top: number; height: number } | null>(null)
  const holdFollowRef = useRef(false)
  const anchorRef = useRef<{ top: number; height: number } | null>(null)
  // 完全访问开关：默认 false（normal 形态）。开启即这次运行跳过毁灭级确认、关沙箱，
  // 随 StartRunInput 的 full_access_ack 提交；粘住直到用户改回来。
  const [full, setFull] = useState(false)
  // 本次运行用的模型（输入区可选）：null = 跟随设置。粘住直到用户改回来——
  // 试模型时通常要连着问几个问题，每条都重选一次很烦。
  const [runModel, setRunModel] = useState<string | null>(null)
  // 工作区候选与「新会话将使用的工作区」选择；新增走宿主机 picker（不可用时手动路径）。
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[] | null>(null)
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  const [wsBusy, setWsBusy] = useState(false)
  const [wsHint, setWsHint] = useState<string | null>(null)
  // 会话动作（新建 / 重命名 / 删除）：在飞时禁用新建，结果或失败都落一行提示。
  const [sessionsBusy, setSessionsBusy] = useState(false)
  const [sessionsHint, setSessionsHint] = useState<string | null>(null)
  // 活运行：一次运行的发送/订阅/终态回拉。它自己的段落属于哪个会话由 hook 记着
  // （live.attachedSession）——切走会话时那些段落不跟过去；attachedRunRef 防重复附着。
  // `?settings=1` 是开发期钉子（截图/联调直达设置界面），与 ?gallery=1 同性质
  const [settingsOpen, setSettingsOpen] = useState(
    () => new URLSearchParams(window.location.search).has('settings'),
  )
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
      // 只回拉用量：条目不再重读——本次运行的段落已经在时间线上（事件流建的），
      // 重读会让它们换个位置出现，那就是跳变。历史的重建留给切会话/刷新。
      try {
        const viewed = branchRef.current
        const bl = await listBranches(id)
        const hit = bl.branches.find((b) => b.name === viewed) ?? bl.branches.find((b) => b.is_default)
        setUsage(hit?.usage ?? null)
      } catch {
        // 回拉失败保留旧视图，刷新兜底
      }
    }
    try {
      const w = await listSessions()
      setSessions(w.sessions)
    } catch {
      // 列表刷新失败不阻塞收尾
    }
    live.settle()
  }

  // 时间线 = 会话历史 + 本次运行的段落。运行段落只属于它自己的会话：
  // 切走会话时只显示目标会话的历史，切回来再并（mergeItems 按 entry_id 去重）。
  const runHere = live.attachedSession === selectedId
  const shown: TimelineItem[] =
    history === null ? [] : runHere ? mergeItems(history, live.items) : history
  // 右列子智能体面板的数据：整条时间线里摊平出来的子运行（含只剩任务清单的历史项）
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
    if (!selectedId) return
    let alive = true
    setHistory(null)
    // 用量快照与条目分属两个端点；快照失败不该连累对话流，静默回退到「—」。
    listEntries(selectedId, { branch, limit: 50 })
      .then((page) => {
        if (!alive) return
        setHistory(itemsFromEntries([...page.entries].reverse()))
        setEarlier({ hasMore: page.has_more, cursor: page.next_cursor })
      })
      .catch((e: unknown) => {
        if (alive) {
          setHistory([])
          setError(e instanceof ApiError ? e.message : String(e))
        }
      })
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
  }, [selectedId, branch])

  const selected = sessions?.find((s) => s.id === selectedId) ?? null

  // 切会话回到主线：分支是「这个会话内部的一条线」，跟到别的会话上是错的。
  useEffect(() => {
    setBranch('main')
    setBranchHint(null)
  }, [selectedId])

  // 切会话把贴底状态拨回默认：上一会话停在顶部时，下一会话也要照常落底（评审 L6）。
  useEffect(() => {
    scroll.reset()
  }, [selectedId])

  /**
   * 从某条消息分叉：建分支 → 切到它。不切的话这个按钮就是个死按钮
   * （前端没有分支选择器），所以顺带给一条「回到主线」的退路。
   */
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

  /** 追加更早历史：先记视口锚点，prepend 后钉回同一条旧消息；失败保留原视图可重试。 */
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
      // 追加失败不打断对话视图；按钮保持可点，用户可重试
      holdFollowRef.current = false
      anchorRef.current = null
    } finally {
      setLoadingEarlier(false)
    }
  }

  // prepend 落地后按锚点差值回滚视口（layout：在浏览器绘制前完成，不闪）。
  useLayoutEffect(() => {
    if (pendingAnchor === null) return
    const el = scroll.ref.current
    if (el !== null) el.scrollTop = pendingAnchor.top + (el.scrollHeight - pendingAnchor.height)
    setPendingAnchor(null)
    holdFollowRef.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只随锚点触发
  }, [pendingAnchor])

  // 默认选择：选中会话归属的工作区，否则第一个候选；用户手动选过就不覆盖。
  useEffect(() => {
    if (activeWorkspaceId || !workspaces) return
    const sw = selected?.workspace?.id
    const fallback = sw && workspaces.some((w) => w.id === sw) ? sw : (workspaces[0]?.id ?? null)
    if (fallback) setActiveWorkspaceId(fallback)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在缺省时填充一次
  }, [workspaces, selected?.workspace?.id, activeWorkspaceId])

  // 会话由项目管理：切换项目后，当前选中不属于它时，切到该项目下的第一个会话。
  useEffect(() => {
    if (!activeWorkspaceId || !sessions) return
    const inProject = sessions.filter((s) => s.workspace?.id === activeWorkspaceId)
    if (selectedId && inProject.some((s) => s.id === selectedId)) return
    setSelectedId(inProject[0]?.id ?? null)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只随项目切换与列表刷新而调整
  }, [activeWorkspaceId, sessions])

  // 选中会话有在跑的运行（刷新 / 切回）：附着到它的流，durable 重放重建运行段落。
  useEffect(() => {
    const active = selected?.active_run_id ?? null
    if (!active || live.phase !== 'idle' || attachedRunRef.current === active) return
    attachedRunRef.current = active
    live.attach(active)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只随会话的活动运行变化而附着
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
        // 409：目录已在列表里——按 detail.id 选中既有项，不当作错误打扰
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

  /** 从项目列表移除：注册表条目，不删磁盘上的会话文件（hint 里照实说）。 */
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

  /** 在当前项目下新建会话并选中它（workspace 是服务端必填项，没有默认工作区）。 */
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

  /** 删除会话（组件内已二次确认）：销毁磁盘记录文件；删的是选中项就换选同项目第一条。 */
  const deleteSessionById = async (id: string) => {
    setSessionsBusy(true)
    setSessionsHint(null)
    try {
      await deleteSession(id)
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

  // 上下文环的读数：运行中吃事件里的每轮快照（所以它是动态的），
  // 其余时候用分支落盘的那份（切会话/刷新都还在）。
  const shownUsage = runHere ? (live.usage ?? usage) : usage
  const liveActive = live.phase === 'starting' || live.phase === 'running' || live.phase === 'settling'
  const liveHere = liveActive && runHere
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
        {/* 一条时间线：历史与本次运行的段落同形，过程与收尾共用它——
            收尾不再换渲染器，也不重排（这是「逐段出现」的另一半）。
            本轮还在跑就铺着过程，跑完由 Timeline 按轮折成一行「已完成，用时 …」。 */}
        {hasItems && (
          <Timeline
            items={shown}
            workspaceRoot={selected?.workspace?.root ?? null}
            liveTail={liveHere}
            onBranch={(id) => void branchFrom(id)}
            onOpenSubagents={() => dock.select('subagents')}
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
      // 右列是常驻的 dock 面板：收起 = 不挂它，栅格自然回到两列（主列于是拿回
      // 那 280px，而不是被浮层盖住）。
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
            onSelect={setSelectedId}
            onCreateSession={() => void createSessionInProject()}
            onRenameSession={(id, name) => void renameSessionById(id, name)}
            onDeleteSession={(id) => void deleteSessionById(id)}
            creating={sessionsBusy}
            notice={sessionsHint}
          />
          {/* 设置入口在侧栏最底部、单开一栏（原先挂在顶栏右上角） */}
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
              />
            </div>
          )}
          <Composer
            full={full}
            onToggleFull={setFull}
            disabled={!selectedId}
            busy={liveHere}
            model={runModel}
            onChangeModel={setRunModel}
            effectiveModel={meta?.capabilities.model ?? null}
            byokModels={meta?.capabilities.models ?? []}
            usage={shownUsage}
            onSend={(text) => void live.send(text, full, runModel, branch)}
            onStop={() => void live.stop()}
          />
        </div>
      }
      />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </>
  )
}
