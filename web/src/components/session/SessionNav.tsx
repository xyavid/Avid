/**
 * 侧栏会话区（参考图）：标题行（「会话」+ 新建）+ 搜索框（30px 高，报告 §7.2）
 * + 会话列表 + 结果提示行。
 * 列表按 created_at 降序；标题取会话名，缺名显示「未命名会话」；
 * 流式中的会话（active_run_id 非空）带呼吸点——真实状态，不是装饰。
 * 分组（置顶/今天/昨天）等后端有置顶概念后再立——现在拍平，不造假分组。
 *
 * 组件只发意图：新建/重命名/删除都回调给装配层（它独占状态与副作用），
 * 因此同一套组件既能接真后端，也能在组件墙里当静态演示（不给回调即只读）。
 */

import { useState } from 'react'

import type { SessionSummary } from '../../api/types'
import { Icon } from '../../ui/Icon'
import { Input } from '../../ui/Input'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { SessionItem } from './SessionItem'

/** 今天 → HH:MM；今年 → M-D HH:MM；更早 → YYYY-M-D（created_at 是毫秒）。 */
function formatStamp(createdAtMs: number): string {
  const d = new Date(createdAtMs)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return hm
  const md = `${d.getMonth() + 1}-${d.getDate()} ${hm}`
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}-${md}`
}

export type SessionNavProps = {
  sessions: SessionSummary[]
  /** 当前项目（工作区）id：列表只显示该项目下的会话；缺省/null = 全部显示（兜底形态）。 */
  workspaceId?: string | null
  selectedId: string | null
  onSelect: (id: string) => void
  /**
   * 在当前项目下新建会话。**workspace 是服务端的必填项**，所以没有选中项目时
   * 按钮禁用并说明；不给回调 = 只读演示（组件墙）。
   */
  onCreateSession?: () => void
  /** 行内重命名（名字已 trim、非空）；不给则该动作不渲染。 */
  onRenameSession?: (id: string, name: string) => void
  /** 删除（组件内已先确认）；不给则该动作不渲染。 */
  onDeleteSession?: (id: string) => void
  /** 新建在飞：按钮落 disabled，避免连点建出几个空会话。 */
  creating?: boolean
  /** 动作结果或失败原因，一句人话（删除不可逆，成功也要说话）。 */
  notice?: string | null
}

export function SessionNav({
  sessions,
  workspaceId = null,
  selectedId,
  onSelect,
  onCreateSession,
  onRenameSession,
  onDeleteSession,
  creating = false,
  notice = null,
}: SessionNavProps) {
  const [query, setQuery] = useState('')
  const listScrollRef = useAutoHideScroll<HTMLDivElement>()
  const needle = query.trim().toLowerCase()
  const inProject = workspaceId ? sessions.filter((s) => s.workspace?.id === workspaceId) : sessions
  const filtered = inProject.filter((s) => (s.name ?? '').toLowerCase().includes(needle))

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-a12">
      <div className="flex items-center justify-between">
        <span className="font-ui text-hint font-medium text-ink-muted">会话</span>
        <button
          type="button"
          onClick={onCreateSession}
          disabled={creating || !workspaceId || !onCreateSession}
          aria-label="新建会话"
          title={
            workspaceId
              ? '在当前项目下新建会话'
              : '先在项目里选择一个项目，再新建会话（归属不可改）'
          }
          className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-sm text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Icon name="plus" size={12} />
        </button>
      </div>
      <Input
        bare
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="搜索会话…"
        aria-label="搜索会话"
        className="h-[30px] rounded-sm bg-overlay-light px-a8"
      />
      <div ref={listScrollRef} className="scroll-auto min-h-0 flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <p className="px-a8 font-ui text-hint text-ink-muted">
            {workspaceId && inProject.length === 0
              ? '这个项目还没有会话'
              : sessions.length === 0
                ? '还没有会话'
                : '没有匹配的会话'}
          </p>
        ) : (
          filtered.map((s) => (
            <SessionItem
              key={s.id}
              title={s.name ?? '未命名会话'}
              meta={formatStamp(s.created_at)}
              active={s.id === selectedId}
              streaming={s.active_run_id !== null}
              onSelect={() => onSelect(s.id)}
              onRename={onRenameSession && ((name) => onRenameSession(s.id, name))}
              onDelete={onDeleteSession && (() => onDeleteSession(s.id))}
            />
          ))
        )}
      </div>
      {notice && <p className="px-a8 font-ui text-micro text-ink-light">{notice}</p>}
    </div>
  )
}
