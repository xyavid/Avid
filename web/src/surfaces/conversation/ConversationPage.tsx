/**
 * 对话表面（阶段 4）：用冻结组件组装完整第一屏，接真后端只读数据
 * （meta / sessions / entries）。发送与流式在阶段 5 接线。
 *
 * 布局职责（报告 §6）：对话列 ≤720px 居中、输入列略宽（chat-input），
 * 由本表面自己排——AppShell 只提供三栏骨架与滚动边界。
 * 数据分页：desc 取最近 50 条后本地反转展示；「加载更早」留阶段 5。
 * truncated_tail（上次运行中断）在流顶给一条提示——派生自投影，不新增字段。
 */

import { useEffect, useState } from 'react'

import { ApiError, getMeta, listBranches, listEntries, listSessions } from '../../api/client'
import type { Entry, Meta, SessionSummary, UsageReport } from '../../api/types'
import { Composer } from '../../components/chat/Composer'
import { Timeline } from '../../components/chat/Timeline'
import { ContextRail } from '../../components/rail/ContextRail'
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

  let body = <Welcome detail="从左侧选择一个会话；发送消息在阶段 5 接线" />
  if (error) {
    body = <p className="px-a8 pt-a8 font-ui text-ui text-danger">{error}</p>
  } else if (selectedId && entries === null) {
    body = <p className="px-a8 font-ui text-hint text-ink-muted">加载中…</p>
  } else if (selectedId && entries !== null && entries.length > 0) {
    body = (
      <>
        {selected?.truncated_tail && (
          <p className="mb-a16 text-center font-ui text-hint text-ink-muted">上次运行在此中断</p>
        )}
        <Timeline entries={entries} />
      </>
    )
  } else if (selectedId && entries !== null && entries.length === 0) {
    body = <Welcome detail="这个会话还没有对话内容" />
  }

  return (
    <AppShell
      sidebar={<SessionNav sessions={sessions ?? []} selectedId={selectedId} onSelect={setSelectedId} />}
      main={
        <div className="mx-auto flex h-full max-w-chat-input flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-a16 pt-a16">{body}</div>
          <Composer />
        </div>
      }
      rail={<ContextRail meta={meta} session={selected} usage={usage} />}
    />
  )
}
