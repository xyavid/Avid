/**
 * 对话表面（阶段 4）：用冻结组件组装完整第一屏，接真后端只读数据
 * （meta / sessions / entries）。发送与流式在阶段 5 接线。
 *
 * 布局职责（报告 §6）：对话列 ≤720px 居中、输入列略宽（chat-input），
 * 由本表面自己排——AppShell 只提供三栏骨架与滚动边界。
 * 数据分页：desc 取最近 50 条后本地反转展示；「加载更早」留阶段 5。
 * truncated_tail（上次运行中断）在流顶给一条提示——派生自投影，不新增字段。
 */

import { useEffect, useRef, useState } from 'react'

import {
  ApiError,
  createWorkspace,
  getMeta,
  listBranches,
  listEntries,
  listSessions,
  listWorkspaces,
  pickFolder,
} from '../../api/client'
import type { Entry, Meta, PermissionMode, SessionSummary, UsageReport, WorkspaceSummary } from '../../api/types'
import { ApprovalBar } from '../../components/chat/ApprovalBar'
import { AssistantMessage } from '../../components/chat/AssistantMessage'
import { Composer } from '../../components/chat/Composer'
import { Timeline, toolIcon } from '../../components/chat/Timeline'
import { ToolCard } from '../../components/chat/ToolCard'
import { UserBubble } from '../../components/chat/UserBubble'
import { useRunStream } from '../../state/useRunStream'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { ContextRail } from '../../components/rail/ContextRail'
import { ProjectCard } from '../../components/session/ProjectCard'
import { SessionNav } from '../../components/session/SessionNav'
import { AppShell } from '../../app/AppShell'

/** 欢迎态（报告 §6 底部欢迎态）：100px 头像圆 + 衬线欢迎语（letter-spacing .06em）。 */
function Welcome({ detail }: { detail: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-a16">
      <span
        aria-hidden
        className="flex h-[100px] w-[100px] items-center justify-center rounded-full border-hairline border-hair bg-card font-serif text-[40px] text-ink-light"
      >
        A
      </span>
      <p className="font-serif text-[20px] tracking-[0.06em] text-ink">有什么可以帮你？</p>
      <p className="font-ui text-hint text-ink-muted">{detail}</p>
    </div>
  )
}

export function ConversationPage() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [entries, setEntries] = useState<Entry[] | null>(null)
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 权限「态势」：随选中会话回落到其工作区的默认权限，用户可在输入区改（下次发送生效）。
  const [permission, setPermission] = useState<PermissionMode>('manual')
  // 工作区候选与「新会话将使用的工作区」选择；新增走宿主机 picker（不可用时手动路径）。
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[] | null>(null)
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  const [wsBusy, setWsBusy] = useState(false)
  const [wsHint, setWsHint] = useState<string | null>(null)
  // 活运行：一次运行的发送/订阅/终态回拉。liveSession 标记活事件属于哪个会话
  // （切走会话时活区块不跟过去）；attachedRunRef 防重复附着同一运行。
  const [liveSession, setLiveSession] = useState<string | null>(null)
  const conversationScrollRef = useAutoHideScroll<HTMLDivElement>()
  const selectedIdRef = useRef<string | null>(null)
  selectedIdRef.current = selectedId
  const attachedRunRef = useRef<string | null>(null)
  const settledRef = useRef<() => Promise<void>>(async () => {})
  const live = useRunStream(selectedId, () => {
    void settledRef.current()
  })
  settledRef.current = async () => {
    const id = selectedIdRef.current
    if (id) {
      try {
        const [page, bl] = await Promise.all([listEntries(id, { limit: 50 }), listBranches(id)])
        setEntries([...page.entries].reverse())
        const main = bl.branches.find((b) => b.name === 'main') ?? bl.branches.find((b) => b.is_default)
        setUsage(main?.usage ?? null)
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
    live.reset()
  }

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
    setEntries(null)
    // 用量快照与条目分属两个端点；快照失败不该连累对话流，静默回退到「—」。
    listEntries(selectedId, { limit: 50 })
      .then((page) => {
        if (alive) setEntries([...page.entries].reverse())
      })
      .catch((e: unknown) => {
        if (alive) {
          setEntries([])
          setError(e instanceof ApiError ? e.message : String(e))
        }
      })
    listBranches(selectedId)
      .then((bl) => {
        if (!alive) return
        const main = bl.branches.find((b) => b.name === 'main') ?? bl.branches.find((b) => b.is_default)
        setUsage(main?.usage ?? null)
      })
      .catch(() => {
        if (alive) setUsage(null)
      })
    return () => {
      alive = false
    }
  }, [selectedId])

  const selected = sessions?.find((s) => s.id === selectedId) ?? null

  useEffect(() => {
    setPermission(selected?.workspace?.default_permission ?? 'manual')
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在切换会话时回落默认值
  }, [selected?.id])

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

  // 选中会话有在跑的运行（刷新 / 切回）：附着到它的流，durable 重放重建视图。
  useEffect(() => {
    const active = selected?.active_run_id ?? null
    if (!active || live.phase !== 'idle' || attachedRunRef.current === active) return
    attachedRunRef.current = active
    setLiveSession(selected?.id ?? null)
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

  const liveActive = live.phase === 'starting' || live.phase === 'running' || live.phase === 'settling'
  const liveHere = liveActive && liveSession === selectedId
  const displayError = error ?? (live.phase === 'error' && liveSession === selectedId ? live.error : null)

  let body = <Welcome detail={activeWorkspaceId ? '这个项目还没有会话' : '从左侧选择一个项目'} />
  if (displayError) {
    body = <p className="px-a8 pt-a8 font-ui text-ui text-danger">{displayError}</p>
  } else if (selectedId && entries === null) {
    body = <p className="px-a8 font-ui text-hint text-ink-muted">加载中…</p>
  } else if (selectedId) {
    const hasEntries = entries !== null && entries.length > 0
    body = (
      <>
        {selected?.truncated_tail && (
          <p className="mb-a16 text-center font-ui text-hint text-ink-muted">上次运行在此中断</p>
        )}
        {hasEntries && <Timeline entries={entries} />}
        {liveHere && (
          <div className="mt-a16 flex flex-col gap-a16">
            {live.userText && <UserBubble>{live.userText}</UserBubble>}
            {live.tools.map((t) => (
              <ToolCard
                key={t.callId}
                icon={toolIcon(t.tool)}
                title={t.tool}
                status={t.status === 'denied' ? 'failed' : t.status}
              />
            ))}
            {(live.assistantText || (!hasEntries && live.tools.length === 0)) && (
              <AssistantMessage streaming>{live.assistantText}</AssistantMessage>
            )}
          </div>
        )}
        {!hasEntries && !liveHere && <Welcome detail="这个会话还没有对话内容" />}
      </>
    )
  }

  return (
    <AppShell
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
            onAddByPath={addByPath}
          />
          <SessionNav
            sessions={sessions ?? []}
            workspaceId={activeWorkspaceId}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>
      }
      main={
        <div className="mx-auto flex h-full max-w-chat-input flex-col">
          <div ref={conversationScrollRef} className="scroll-auto flex min-h-0 flex-1 flex-col overflow-y-auto px-a16 pt-a16">
            {body}
          </div>
          {live.approvals.length > 0 && liveSession === selectedId && (
            <div className="px-a16 pb-a8">
              <ApprovalBar
                approvals={live.approvals}
                busy={false}
                onDecide={(id, decision) => void live.decide(id, decision)}
              />
            </div>
          )}
          <Composer
            permission={permission}
            onChangePermission={setPermission}
            disabled={!selectedId}
            busy={liveHere}
            onSend={(text) => {
              setLiveSession(selectedId)
              void live.send(text, permission)
            }}
            onStop={() => void live.stop()}
          />
        </div>
      }
      rail={<ContextRail usage={usage} />}
    />
  )
}
