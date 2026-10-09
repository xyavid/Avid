/**
 * 侧栏会话区（参考图）：标题行（「会话」+ 新建）+ 搜索框（30px 高，报告 §7.2）
 * + 会话列表 + 内容命中 + 结果提示行。
 *
 * 搜索框是**双路**的（阶段 57）：本地按名字过滤（即时，不花一次请求），同时把词交给
 * 装配层去查内容索引（内容命中由 `contentHits` 传进来）——两条结果分开展示，因为
 * 「名字里有」和「聊过这个」是两件事。组件仍然只发意图：查询词、选中项都回调出去。
 * 列表按 created_at 降序；标题取会话名，缺名显示「未命名会话」；
 * 流式中的会话（active_run_id 非空）带呼吸点——真实状态，不是装饰。
 * 分组（置顶/今天/昨天）等后端有置顶概念后再立——现在拍平，不造假分组。
 *
 * 组件只发意图：新建/重命名/删除都回调给装配层（它独占状态与副作用），
 * 因此同一套组件既能接真后端，也能在组件墙里当静态演示（不给回调即只读）。
 */

import { useState } from 'react'

import type { SearchHit, SessionSummary } from '../../api/types'
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
  /** 内容检索的结果（装配层去查索引）；空数组 = 没有内容命中。 */
  contentHits?: SearchHit[]
  /** 内容命中还在查：给一行「正在查…」，别让人以为没结果。 */
  contentSearching?: boolean
  /** 内容命中里点了一条：装配层负责切会话并跳到那条条目。 */
  onSelectHit?: (hit: SearchHit) => void
  /** 查询词变化（装配层据此去查内容索引，自己做防抖）。 */
  onSearchQuery?: (query: string) => void
  /** 内容检索那一路的提示：检索失败、或索引还落后（别让人以为搜全了）。 */
  searchNotice?: string | null
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
  contentHits = [],
  contentSearching = false,
  onSelectHit,
  onSearchQuery,
  searchNotice = null,
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
        onChange={(e) => {
          setQuery(e.target.value)
          onSearchQuery?.(e.target.value)
        }}
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
      {query.trim() !== '' && (contentSearching || contentHits.length > 0 || searchNotice) && (
        <div className="border-t border-hair pt-a8">
          <p className="px-a8 font-ui text-micro text-ink-muted">
            {contentSearching ? '正在查内容…' : `内容命中 ${contentHits.length} 条`}
          </p>
          {searchNotice && (
            <p className="px-a8 font-ui text-micro text-ink-muted">{searchNotice}</p>
          )}
          {contentHits.map((hit) => (
            <button
              key={`${hit.session_id}:${hit.entry_id}`}
              type="button"
              data-hit={hit.entry_id}
              onClick={() => onSelectHit?.(hit)}
              className="mt-a4 block w-full rounded-sm px-a8 py-a4 text-left transition-colors duration-fast ease-out hover:bg-overlay-light"
            >
              <span className="font-ui text-micro text-ink-muted">
                {hit.title ?? '未命名会话'} · {hit.role ?? hit.entry_type}
              </span>
              <span className="mt-a2 line-clamp-2 block font-ui text-hint text-ink">
                {hit.snippet}
              </span>
            </button>
          ))}
        </div>
      )}
      {notice && <p className="px-a8 font-ui text-micro text-ink-light">{notice}</p>}
    </div>
  )
}
