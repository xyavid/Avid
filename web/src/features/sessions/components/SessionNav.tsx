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
 *
 * ## 分组由"按工作区"改成"按时间"，工作区下沉为行内标记
 *
 * 参考截图里左栏是「对话」+ 今天 / 更早，所以外层分组换成时间桶；工作区归属
 * **不作为第二层折叠**，而是变成每条会话元信息行里的一个小标记。为什么不做两级嵌套：
 * 240px 宽的栏里两级缩进之后每层只剩百来像素，"哪个组头属于哪条会话"只能靠缩进猜，
 * 折叠箭头也会挤在一起；而且工作区归属本来就更像"这条会话的属性"而不是"另一个目录树"。
 * 代价是同一工作区的会话不再视觉聚成一簇——需要按项目看时，搜索与工作区管理面板
 * 是更顺的两条路（这也是把「管理工作区」放进底部操作行的原因）。
 */

import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'

import type { SessionSummary, WorkspaceSummary } from '../../../api/types'
import { PanelLeftIcon } from '../../../ui/icons'
import { Button, cx } from '../../../ui/primitives'
import {
  filterSessions,
  groupByBucket,
  sessionLabel,
  sessionWorkspaceLabel,
} from '../lib/navTree'
import { DeleteSessionDialog } from './DeleteSessionDialog'
import { NavSearch } from './NavSearch'
import { SessionListHeader } from './SessionListHeader'
import { SessionItem } from './SessionItem'

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
  /**
   * 「管理工作区」入口。可选：上层没接这条链时不渲染那个按钮，
   * 而不是给一个点了没反应的假入口。
   */
  onManageWorkspaces?: () => void
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
  onManageWorkspaces,
}: SessionNavProps): ReactElement {
  const [query, setQuery] = useState('')
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null)

  // 过滤是纯函数，用 useMemo 钉住引用：列表每次重渲染都要重算，
  // 而 sessions/query 两者不变时结果必然相同。
  const visible = useMemo(() => filterSessions(sessions, query), [sessions, query])
  const searching = query.trim() !== ''

  /*
   * 分桶要用"现在"，而 `now` 是上层给的可选注入点（会话项里的相对时间也只认它，
   * 见 SessionItem）。这里在缺省时取一次系统时钟，理由是分桶标签本身**就是**随时钟
   * 移动的视图事实：上层没传 now 就让整块分组消失，等于把一个已经做出来的功能
   * 留在死代码里。取值不放进 useMemo 的依赖之外，是为了让它随渲染刷新
   * （跨过午夜后"今天"能自己翻档）；代价是这一处渲染不纯，而这条取舍只在
   * 分桶标签上成立——别把 Date.now() 扩散到会话项的相对时间里去。
   */
  const buckets = groupByBucket(visible, now ?? Date.now())

  function requestDelete(id: string): void {
    // 找不到就什么都不做：把 undefined 塞进弹窗会让"删除"按钮操作一个不存在的会话
    setPendingDelete(sessions.find((session) => session.id === id) ?? null)
  }

  return (
    <nav aria-label="会话" className="flex h-full min-h-0 w-full flex-col bg-deep text-ink">
      <div className="avid-hair-b flex items-center gap-a4 px-a8 py-a6">
        {/* busy 不接任何东西：SessionNavProps 里没有"正在创建会话"的状态位，
            Header 的 busy 留给将来真有创建中的信号时再用。
            栏标题按截图给「对话」；缺省值仍是 task-3 的「会话」，不传的调用点行为不变。 */}
        <SessionListHeader
          title="对话"
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
              <div className="flex flex-col gap-a8">
                {buckets.map((bucket) => (
                  <section
                    key={bucket.bucket}
                    aria-label={bucket.label}
                    className="flex flex-col gap-a2"
                  >
                    <p className="px-a8 text-hint text-ink-faint">{bucket.label}</p>
                    <ul className="flex flex-col gap-a2">
                      {bucket.sessions.map((session) => (
                        <SessionItem
                          key={session.id}
                          session={session}
                          active={session.id === activeSessionId}
                          running={runningSessionIds.has(session.id)}
                          workspaceLabel={sessionWorkspaceLabel(session, workspaces)}
                          onSelect={onSelect}
                          onRename={onRename}
                          onDelete={requestDelete}
                          now={now}
                        />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            ) : null}
          </div>
        </>
      )}

      {/* 底部操作行（截图里那一行）。搜索留在列表**上方**：把它挪到下面会让它离
          "正在被搜的列表"更远，而这一行要的是收尾动作——管理工作区与收起侧栏。
          折叠态不渲染：48px 放不下两个按钮，且头部已经有同一个收起/展开按钮，
          再来一个只会让"点哪个"变成猜谜。
          收起按钮的文案与头部那颗不同（「收起侧栏」/「收起会话列表」）：
          同一屏里两个控件说同一句话，会让读屏与按名定位的用例都变得含糊。 */}
      {collapsed ? null : (
        <div className="avid-hair-t flex items-center justify-end gap-a4 px-a8 py-a6">
          {onManageWorkspaces === undefined ? null : (
            <Button
              variant="ghost"
              size="sm"
              type="button"
              className="mr-auto"
              onClick={onManageWorkspaces}
            >
              管理工作区
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            type="button"
            aria-label="收起侧栏"
            onClick={onToggleCollapsed}
            icon={<PanelLeftIcon size={14} />}
          />
        </div>
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
