/**
 * 一次运行的生命周期 hook（发送 → 订阅 → 活事件叠加 → 终态回拉）。
 *
 * 阶段：idle → starting（POST run）→ running（SSE 订阅中）→ settling
 * （收到终态事件，页面回拉 durable 后调 reset 回 idle）；error = 发送/订阅失败。
 * 活事件规则：
 * - durable 消息事件（user/assistant_message）重建视图——attach 到进行中的运行时
 *   靠重放；assistant 的最终消息取代 delta 累积（deltas 永不重放）；
 * - 工具行由 tool_call_started 登记、finished/denied 迁移状态；
 * - 审批入列/出列；终态（finished/failed/cancelled）→ settling + onSettled()
 * - reasoning_delta 单独累积（思考 ≠ 正文）：它属于 delta 档，不落盘、不重放，
 *   只在流里存在——所以刷新或切走会话后就没了，这是刻意的（见 ReasoningBlock）
 *   （页面回拉 entries/usage/sessions 后调 reset）。
 * 流异常断开：轮询 getRun 到终态（web-ui §终端兜底），不无限重连。
 */

import { useCallback, useRef, useState } from 'react'

import { cancelRun, decideApproval, getRun, startRun } from '../api/client'
import { subscribeRun } from '../api/events'
import type { PermissionMode } from '../api/types'

export type RunPhase = 'idle' | 'starting' | 'running' | 'settling' | 'error'

export type LiveTool = { callId: string; tool: string; status: 'running' | 'ok' | 'failed' | 'denied' }

export type LiveApproval = { approvalId: string; tool: string; arguments: string; reason: string }

type EventFrame = { type: string; seq: number | null; data: Record<string, unknown> }

export type RunStream = ReturnType<typeof useRunStream>

export function useRunStream(sessionId: string | null, onSettled: () => void) {
  const [phase, setPhase] = useState<RunPhase>('idle')
  const [runId, setRunId] = useState<string | null>(null)
  const [userText, setUserText] = useState<string | null>(null)
  const [assistantText, setAssistantText] = useState('')
  const [reasoning, setReasoning] = useState('')
  const [tools, setTools] = useState<LiveTool[]>([])
  const [approvals, setApprovals] = useState<LiveApproval[]>([])
  const [error, setError] = useState<string | null>(null)

  const runIdRef = useRef<string | null>(null)
  const subRef = useRef<{ abort: () => void } | null>(null)
  const settlingRef = useRef(false)

  const reset = useCallback(() => {
    subRef.current?.abort()
    subRef.current = null
    runIdRef.current = null
    settlingRef.current = false
    setRunId(null)
    setUserText(null)
    setAssistantText('')
    setReasoning('')
    setTools([])
    setApprovals([])
    setError(null)
    setPhase('idle')
  }, [])

  const handleEvent = useCallback(
    (e: EventFrame) => {
      const data = (e.data ?? {}) as Record<string, unknown>
      switch (e.type) {
        case 'user_message': {
          const msg = data.message as { content?: unknown } | undefined
          if (msg && typeof msg.content === 'string' && msg.content) {
            setUserText((cur) => cur ?? msg.content as string)
          }
          break
        }
        case 'assistant_message': {
          // 最终消息取代 delta 累积（attach 重放时 delta 已丢失，durable 是权威）
          const msg = data.message as { content?: unknown } | undefined
          if (msg && typeof msg.content === 'string' && msg.content.trim()) {
            setAssistantText(msg.content)
          }
          break
        }
        case 'tool_call_started': {
          const callId = String(data.tool_call_id ?? '')
          setTools((ts) =>
            ts.some((t) => t.callId === callId)
              ? ts
              : [...ts, { callId, tool: String(data.tool ?? ''), status: 'running' as const }],
          )
          break
        }
        case 'tool_call_finished': {
          const callId = String(data.tool_call_id ?? '')
          setTools((ts) =>
            ts.map((t) => (t.callId === callId ? { ...t, status: data.status === 'ok' ? ('ok' as const) : ('failed' as const) } : t)),
          )
          break
        }
        case 'tool_call_denied': {
          const callId = String(data.tool_call_id ?? '')
          setTools((ts) => ts.map((t) => (t.callId === callId ? { ...t, status: 'denied' as const } : t)))
          break
        }
        case 'assistant_delta': {
          if (typeof data.text === 'string') setAssistantText((cur) => cur + (data.text as string))
          break
        }
        case 'reasoning_delta': {
          // 思考与正文分开累积：它们在同一次运行里交替到达，混在一起会串行
          if (typeof data.text === 'string') setReasoning((cur) => cur + (data.text as string))
          break
        }
        case 'approval_requested': {
          setApprovals((a) => [
            ...a,
            {
              approvalId: String(data.approval_id ?? ''),
              tool: String(data.tool ?? ''),
              arguments: String(data.arguments ?? ''),
              reason: String(data.reason ?? ''),
            },
          ])
          break
        }
        case 'approval_resolved': {
          const id = String(data.approval_id ?? '')
          setApprovals((a) => a.filter((x) => x.approvalId !== id))
          break
        }
        case 'run_finished':
        case 'run_failed':
        case 'run_cancelled': {
          if (settlingRef.current) break
          settlingRef.current = true
          if (e.type === 'run_failed') setError(String(data.message ?? '运行失败'))
          setPhase('settling')
          onSettled()
          break
        }
        default:
          break
      }
    },
    [onSettled],
  )

  const pollUntilTerminal = useCallback(
    async (id: string) => {
      for (;;) {
        try {
          const run = await getRun(id)
          if (['finished', 'failed', 'cancelled'].includes(run.status)) break
        } catch {
          break // 连 getRun 都够不着：放弃轮询，用户刷新兜底
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
      if (!settlingRef.current) {
        settlingRef.current = true
        setPhase('settling')
        onSettled()
      }
    },
    [onSettled],
  )

  const onStreamError = useCallback(
    (err: Error) => {
      const id = runIdRef.current
      if (!id || settlingRef.current) return
      setError(err.message)
      void pollUntilTerminal(id)
    },
    [pollUntilTerminal],
  )

  const subscribe = useCallback(
    (id: string) => {
      void subscribeRun(id, 0, { onEvent: handleEvent, onError: onStreamError }).then((sub) => {
        subRef.current = sub
      })
    },
    [handleEvent, onStreamError],
  )

  const send = useCallback(
    async (prompt: string, permission: PermissionMode) => {
      if (!sessionId) return
      setPhase('starting')
      setError(null)
      setUserText(prompt)
      setAssistantText('')
      setReasoning('')
      setTools([])
      setApprovals([])
      try {
        const created = await startRun(sessionId, {
          prompt,
          permission,
          ...(permission === 'full' ? { full_access_ack: true } : {}),
        })
        runIdRef.current = created.run_id
        setRunId(created.run_id)
        setPhase('running')
        subscribe(created.run_id)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setPhase('error')
      }
    },
    [sessionId, subscribe],
  )

  const attach = useCallback(
    (id: string) => {
      runIdRef.current = id
      setRunId(id)
      setPhase('running')
      subscribe(id)
    },
    [subscribe],
  )

  const stop = useCallback(async () => {
    const id = runIdRef.current
    if (id) await cancelRun(id).catch(() => {})
  }, [])

  const decide = useCallback(
    async (approvalId: string, decision: 'allow' | 'deny') => {
      const id = runIdRef.current
      if (!id) return
      await decideApproval(id, approvalId, decision).catch(() => {})
    },
    [],
  )

  return {
    phase,
    runId,
    userText,
    assistantText,
    reasoning,
    tools,
    approvals,
    error,
    send,
    stop,
    decide,
    attach,
    reset,
  }
}
