/**
 * 会话导航列（左栏）的组装层。
 *
 * 职责边界：
 *  · 数据（sessions / workspaces / activeSessionId / runningSessionIds）全部由外壳传入，
 *    本组件**不取数、不订阅**——它只把数据折成该看的形状；
 *  · 唯一自持的状态是搜索词与"待删除的会话"，两者都不超出这一栏；
 *  · `collapsed` 只决定渲染密度，宽度由外壳给（`--avid-nav-w` / `--avid-nav-w-collapsed`）。
 *
 * 删除的确认流转在本层闭环：行内删除按钮 → 找到那只会话 → 弹确认 → 确认后把 id
 * 交给上层。上层调 DELETE 若被 409 拒绝，把消息放进 `error` 就能显示在底部——
 * 本层不猜服务端结果，也不做乐观移除（乐观移除撞上 409 会先"消失"再"回来"）。
 */

import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'

import type { SessionSummary, WorkspaceSummary } from '../../../api/types'
import { cx } from '../../../ui/primitives'
import { filterSessions, groupSessions, sessionLabel } from '../lib/navTree'
import { DeleteSessionDialog } from './DeleteSessionDialog'
import { NavSearch } from './NavSearch'
import { SessionListHeader } from './SessionListHeader'
import { WorkspaceFolder } from './WorkspaceFolder'

export interface SessionNavProps {
  sessions: SessionSummary[]
  workspaces: WorkspaceSummary[]
  activeSessionId: string | null
  runningSessionIds: ReadonlySet<string>
  collapsed: boolean
  error: string | null
  onToggleCollapsed: () => void
  onSelect: (id: string) => void
  onNewSession: () => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
  /** 相对时间的"现在"，透传给会话项；不传则不显示时间。 */
  now?: number
}

/** 折叠态头像取首字：中文取首字，拉丁名取大写首字母；未命名会话没有名字，给 #。 */
function avatarInitial(session: SessionSummary): string {
  const name = session.name?.trim()
  if (!name) return '#'
  return name.charAt(0).toUpperCase()
}

export function SessionNav({
  sessions,
  workspaces,
  activeSessionId,
  runningSessionIds,
  collapsed,
  error,
  onToggleCollapsed,
  onSelect,
  onNewSession,
  onRename,
  onDelete,
  now,
}: SessionNavProps): ReactElement {
  const [query, setQuery] = useState('')
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null)

  // 过滤与分组都是纯函数，用 useMemo 钉住引用：列表每次重渲染都要重算，
  // 而 sessions/workspaces/query 三者不变时结果必然相同。
  const visible = useMemo(() => filterSessions(sessions, query), [sessions, query])
  const groups = useMemo(() => groupSessions(visible, workspaces), [visible, workspaces])
  const searching = query.trim() !== ''

  function requestDelete(id: string): void {
    // 找不到就什么都不做：把 undefined 塞进弹窗会让"删除"按钮操作一个不存在的会话
    setPendingDelete(sessions.find((session) => session.id === id) ?? null)
  }

  return (
    <nav aria-label="会话" className="flex h-full min-h-0 w-full flex-col bg-deep text-ink">
      <div className="avid-hair-b flex items-center gap-a4 px-a8 py-a6">
        {/* busy 不接任何东西：SessionNavProps 里没有"正在创建会话"的状态位
            （rev3 也去掉了 loading），Header 的 busy 留给将来真有创建中的信号时再用。 */}
        <SessionListHeader
          onNewSession={onNewSession}
          onToggleCollapsed={onToggleCollapsed}
          collapsed={collapsed}
        />
      </div>

      {collapsed ? (
        /* 折叠态：只剩头像式首字母列表。标题、搜索、分组头全部让位给宽度——
           48px 里放不下任何一行可用文字，硬塞只会变成一排省略号。 */
        <ul className="flex min-h-0 flex-1 flex-col items-center gap-a4 overflow-y-auto px-a4 py-a8">
          {sessions.map((session) => {
            const active = session.id === activeSessionId
            return (
              <li key={session.id}>
                <button
                  type="button"
                  aria-label={sessionLabel(session)}
                  aria-current={active ? 'true' : undefined}
                  title={sessionLabel(session)}
                  onClick={() => onSelect(session.id)}
                  className={cx(
                    'flex h-a24 w-a24 items-center justify-center rounded-full text-hint transition-colors duration-fast',
                    active
                      ? 'bg-accent-soft font-medium text-accent'
                      : 'text-ink-muted hover:bg-accent-soft hover:text-ink',
                  )}
                >
                  {avatarInitial(session)}
                </button>
              </li>
            )
          })}
        </ul>
      ) : (
        <>
          <NavSearch value={query} onChange={setQuery} />

          <div className="min-h-0 flex-1 overflow-y-auto px-a8 py-a6">
            {/* 两种"空"要分开说：一条会话都没有 / 搜索没命中。
                混成一句"没有会话"会让用户以为数据没了。
                加载态不在这里表达（rev3 的 SessionNavProps 没有 loading）：
                "数据还在路上"是上层是否渲染本组件的决定，导航列重复一份只会两边打架。 */}
            {visible.length === 0 ? (
              <>
                <p className="px-a8 py-a12 text-hint text-ink-muted">
                  {searching ? '没有匹配的会话' : '还没有会话'}
                </p>
                {searching ? null : (
                  <p className="px-a8 text-hint text-ink-faint">
                    点右上角的加号新建会话，或从命令行发起一次运行。
                  </p>
                )}
              </>
            ) : null}

            {visible.length > 0 ? (
              <ul className="flex flex-col gap-a4">
                {groups.map((group) => (
                  <WorkspaceFolder
                    key={group.key}
                    group={group}
                    activeSessionId={activeSessionId}
                    runningSessionIds={runningSessionIds}
                    onSelect={onSelect}
                    onRename={onRename}
                    onDelete={requestDelete}
                    now={now}
                  />
                ))}
              </ul>
            ) : null}
          </div>
        </>
      )}

      {/* 错误行贴在栏底、常驻：列表仍可读（错误往往只影响一次操作），
          所以不能用一个盖住列表的横幅。role=status 让读屏在它出现时播报一次。 */}
      {error ? (
        <p role="status" className="avid-hair-t px-a8 py-a6 text-hint text-danger">
          {error}
        </p>
      ) : null}

      <DeleteSessionDialog
        session={pendingDelete}
        onCancel={() => setPendingDelete(null)}
        onConfirm={(id) => {
          // 先关弹窗再交给上层：本层拿不到请求结果，弹窗留在原地转圈反而像卡住了；
          // 失败会从 error 回到栏底
          setPendingDelete(null)
          onDelete(id)
        }}
      />
    </nav>
  )
}
