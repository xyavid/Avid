/**
 * Sidebar session area: title row + search box (30px high) + session list + content hits; search is
 * two-track — local name filtering is immediate while the query also goes through the assembly
 * layer for the content index, whose hits arrive via `contentHits`.
 * The component emits intents only (query, selection, session actions call back) and renders
 * read-only without callbacks; list order is created_at desc.
 */

import { useState } from 'react'

import type { SearchHit, SessionSummary } from '../../api/types'
import { Icon } from '../../ui/Icon'
import { Input } from '../../ui/Input'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { SessionItem } from './SessionItem'

/** Today → HH:MM; this year → M-D HH:MM; earlier → YYYY-M-D (created_at is milliseconds). */
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
  /** Current project (workspace) id: only its sessions are listed; null = show all as fallback. */
  workspaceId?: string | null
  selectedId: string | null
  onSelect: (id: string) => void
  /** Create a session in the current project; the server requires `workspace`, so the button is
   * disabled without a selected project. */
  onCreateSession?: () => void
  /** Inline rename (trimmed, non-empty name); the action is not rendered without it. */
  onRenameSession?: (id: string, name: string) => void
  /** Delete (already confirmed inside the component); not rendered without it. */
  onDeleteSession?: (id: string) => void
  /** Content-search hits (the assembly layer queries the index); empty array = no hits. */
  contentHits?: SearchHit[]
  /** Content hits still loading: a line says so rather than looking like "no results". */
  contentSearching?: boolean
  /** A hit was clicked: the assembly layer switches session and jumps to that entry. */
  onSelectHit?: (hit: SearchHit) => void
  /** Query changed; the assembly layer debounces it against the content index. */
  onSearchQuery?: (query: string) => void
  /** Notice for content search: a failure, or the index lagging behind. */
  searchNotice?: string | null
  /** Create in flight: the button goes disabled so double-clicks cannot make empty sessions. */
  creating?: boolean
  /** Action result or failure reason in plain words; success is stated too, deletion is final. */
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
