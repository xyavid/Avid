/**
 * 侧栏会话区（参考图）：搜索框（30px 高，报告 §7.2）+ 会话列表。
 * 列表按 created_at 降序；标题取会话名，缺名显示「未命名会话」；
 * 流式中的会话（active_run_id 非空）带呼吸点——真实状态，不是装饰。
 * 分组（置顶/今天/昨天）等后端有置顶概念后再立——现在拍平，不造假分组。
 */

import { useState } from 'react'

import type { SessionSummary } from '../../api/types'
import { Input } from '../../ui/Input'
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
  selectedId: string | null
  onSelect: (id: string) => void
}

export function SessionNav({ sessions, selectedId, onSelect }: SessionNavProps) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const filtered = sessions.filter((s) => (s.name ?? '').toLowerCase().includes(needle))

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-a12">
      <Input
        bare
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="搜索会话…"
        aria-label="搜索会话"
        className="h-[30px] rounded-sm bg-overlay-light px-a8"
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <p className="px-a8 font-ui text-hint text-ink-muted">
            {sessions.length === 0 ? '还没有会话' : '没有匹配的会话'}
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
            />
          ))
        )}
      </div>
    </div>
  )
}
