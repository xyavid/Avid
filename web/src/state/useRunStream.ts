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

import { useCallback, useEffect, useRef, useState } from 'react'

import { cancelRun, decideApproval, getRun, startRun } from '../api/client'
import { subscribeRun } from '../api/events'
import type { PermissionMode } from '../api/types'

export type RunPhase = 'idle' | 'starting' | 'running' | 'settling' | 'error'

export type LiveTool = {
  callId: string
  tool: string
  status: 'running' | 'ok' | 'failed' | 'denied'
  /** 调用参数（JSON 串）——活卡片预览用，来自 tool_call_started。 */
  arguments: string
  /** 工具结果（tool_result_message 落地后填入）；null = 结果未到。 */
  result: string | null
}

/**
 * 活区块的有序段：思考与工具按事件流的先后交错（ZCode 式时间线），不再
 * 「所有工具一行 + 一大块思考」。相邻思考段合并；工具段逐卡独立。
 */
export type LiveSegment =
  | { kind: 'reasoning'; text: string }
  | ({ kind: 'tool'; callId: string; tool: string } & Omit<LiveTool, 'callId' | 'tool'>)

export type LiveApproval = { approvalId: string; tool: string; arguments: string; reason: string }

type EventFrame = { type: string; seq: number | null; data: Record<string, unknown> }

export type RunStream = ReturnType<typeof useRunStream>

export function useRunStream(sessionId: string | null, onSettled: () => void) {
  const [phase, setPhase] = useState<RunPhase>('idle')
  const [runId, setRunId] = useState<string | null>(null)
  const [userText, setUserText] = useState<string | null>(null)
  const [assistantText, setAssistantText] = useState('')
  const [segments, setSegments] = useState<LiveSegment[]>([])
  const [approvals, setApprovals] = useState<LiveApproval[]>([])
  const [error, setError] = useState<string | null>(null)

  const runIdRef = useRef<string | null>(null)
  const subRef = useRef<{ abort: () => void } | null>(null)
  const settlingRef = useRef(false)
  // delta 合帧：增量文本先进 ref，rAF 每帧至多刷一次 state——流式重渲染从
  // 「每 token 两次」（assistant/reasoning 各一次）降到「每帧至多一次」，
  // Markdown 的重解析随之合帧（它是按文本记忆化的，state 不变就不重算）。
  // 哨兵用独立布尔而非帧句柄：句柄赋值发生在 rAF 注册之后，同步执行的
  // 测试桩会把「已消费」的句柄覆盖回非空，卡死后续所有增量。
  // 后台标签页 rAF 停发，另挂 1s 定时器兜底：增量最迟一秒落 state，
  // 不丢不重，也不会在隐藏页里无界积压（评审 L5）。
  const pendingFrameRef = useRef(false)
  const frameHandleRef = useRef<number | null>(null)
  const fallbackTimerRef = useRef<number | null>(null)
  const pendingAssistantRef = useRef('')
  const flushDeltas = useCallback(() => {
    pendingFrameRef.current = false
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current)
      fallbackTimerRef.current = null
    }
    if (pendingAssistantRef.current) {
      const text = pendingAssistantRef.current
      pendingAssistantRef.current = ''
      setAssistantText((cur) => cur + text)
    }
  }, [])
  const scheduleDeltaFlush = useCallback(() => {
    if (pendingFrameRef.current) return
    pendingFrameRef.current = true
    const flush = () => {
      pendingFrameRef.current = false
      if (frameHandleRef.current !== null) {
        cancelAnimationFrame(frameHandleRef.current)
        frameHandleRef.current = null
      }
      if (fallbackTimerRef.current !== null) {
        window.clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
      flushDeltas()
    }
    frameHandleRef.current = requestAnimationFrame(flush)
    fallbackTimerRef.current = window.setTimeout(flush, 1000)
  }, [flushDeltas])
  // 最终消息/重置取代 delta 累积：未刷帧的增量必须作废，否则终态文本后面
  // 会再接一截旧增量。
  const discardPendingDeltas = useCallback(() => {
    pendingFrameRef.current = false
    if (frameHandleRef.current !== null) {
      cancelAnimationFrame(frameHandleRef.current)
      frameHandleRef.current = null
    }
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current)
      fallbackTimerRef.current = null
    }
    pendingAssistantRef.current = ''
  }, [])
  useEffect(() => () => discardPendingDeltas(), [discardPendingDeltas])

  const reset = useCallback(() => {
    subRef.current?.abort()
    subRef.current = null
    runIdRef.current = null
    settlingRef.current = false
    discardPendingDeltas()
    setRunId(null)
    setUserText(null)
    setAssistantText('')
    setSegments([])
    setApprovals([])
    setError(null)
    setPhase('idle')
  }, [discardPendingDeltas])

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
          discardPendingDeltas()
          const msg = data.message as { content?: unknown } | undefined
          if (msg && typeof msg.content === 'string' && msg.content.trim()) {
            setAssistantText(msg.content)
          }
          break
        }
        case 'tool_call_started': {
          const callId = String(data.tool_call_id ?? '')
          setSegments((segs) =>
            segs.some((s) => s.kind === 'tool' && s.callId === callId)
              ? segs
              : [
                  ...segs,
                  {
                    kind: 'tool' as const,
                    callId,
                    tool: String(data.tool ?? ''),
                    status: 'running' as const,
                    arguments: JSON.stringify(data.arguments ?? {}),
                    result: null,
                  },
                ],
          )
          break
        }
        case 'tool_call_finished': {
          const callId = String(data.tool_call_id ?? '')
          setSegments((segs) =>
            segs.map((s) =>
              s.kind === 'tool' && s.callId === callId
                ? { ...s, status: data.status === 'ok' ? ('ok' as const) : ('failed' as const) }
                : s,
            ),
          )
          break
        }
        case 'tool_call_denied': {
          const callId = String(data.tool_call_id ?? '')
          setSegments((segs) =>
            segs.map((s) => (s.kind === 'tool' && s.callId === callId ? { ...s, status: 'denied' as const } : s)),
          )
          break
        }
        case 'tool_result_message': {
          // 结果以 durable 消息事件落地（重放也会出现）：按 callId 归位到工具段。
          const msg = data.message as { tool_call_id?: unknown; content?: unknown } | undefined
          if (!msg || typeof msg.tool_call_id !== 'string') break
          const callId = msg.tool_call_id
          const content =
            typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content ?? '')
          setSegments((segs) =>
            segs.map((s) => (s.kind === 'tool' && s.callId === callId ? { ...s, result: content } : s)),
          )
          break
        }
        case 'assistant_delta': {
          if (typeof data.text === 'string') {
            pendingAssistantRef.current += data.text
            scheduleDeltaFlush()
          }
          break
        }
        case 'reasoning_delta': {
          // 思考即时写段（不进合帧）：合帧按帧落 state，帧的时机会让「先思考后
          // 调工具」的段落错序。ReasoningBlock 是纯文本渲染，逐 delta 更新便宜；
          // 需要合帧的是走 Markdown 的 assistant 增量。相邻思考段合并为一块。
          if (typeof data.text === 'string' && data.text) {
            const text = data.text
            setSegments((segs) => {
              const last = segs.at(-1)
              if (last !== undefined && last.kind === 'reasoning') {
                return [...segs.slice(0, -1), { kind: 'reasoning' as const, text: last.text + text }]
              }
              return [...segs, { kind: 'reasoning' as const, text }]
            })
          }
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
    [discardPendingDeltas, onSettled, scheduleDeltaFlush],
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
    async (prompt: string, permission: PermissionMode, model?: string | null, branch?: string) => {
      if (!sessionId) return
      setPhase('starting')
      setError(null)
      discardPendingDeltas()
      setUserText(prompt)
      setAssistantText('')
      setSegments([])
      setApprovals([])
      try {
        const created = await startRun(sessionId, {
          prompt,
          permission,
          // 只在选了覆盖时才带 model：不带 = 服务端按设置解析（与旧行为逐字一致）
          ...(model ? { model } : {}),
          // 只在非主线时才带 branch：不带 = 服务端默认 main，载荷与旧行为逐字一致
          ...(branch && branch !== 'main' ? { branch } : {}),
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
    [discardPendingDeltas, sessionId, subscribe],
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

  // Dock 进程面板用的扁平工具表：从有序段派生，状态与结果随段实时更新。
  const tools: LiveTool[] = segments.flatMap((s) =>
    s.kind === 'tool'
      ? [{ callId: s.callId, tool: s.tool, status: s.status, arguments: s.arguments, result: s.result }]
      : [],
  )

  return {
    phase,
    runId,
    userText,
    assistantText,
    segments,
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
