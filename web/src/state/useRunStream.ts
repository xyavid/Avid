/**
 * 一次运行的生命周期 hook（发送 → 订阅 → 段落归并 → 终态收尾）。
 *
 * 阶段：idle → starting（POST run）→ running（SSE 订阅中）→ settling（收到终态事件，
 * 页面回拉 durable 后调 settle 回 idle）；error = 发送/订阅失败。
 *
 * 它持有的是**本次运行产出的段落**（`items`），不是整个会话的时间线：页面把
 * 这份段落并回会话历史（`timeline.mergeItems`），过程与收尾共用同一个渲染器。
 * 事件怎么变成段落全在 `timeline.applyEvent`（纯函数，按 entry_id / tool_call_id
 * 幂等——中途刷新附着会从 seq 0 重放，重复投递不能变成重复段落）。
 *
 * 收尾**不清 items**：清了就等于「过程一个样、最后另起一个样」，那正是这次要治的病。
 * 段落一直留到会话切换/刷新（那时页面按会话条目重建，live-only 段——思考与子 agent
 * 步骤——随之消失，见 timeline.ts 的注释）。
 *
 * 流异常断开：轮询 getRun 到终态（终端兜底），不无限重连。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { answerApproval, cancelRun, decideApproval, getRun, startRun } from '../api/client'
import { subscribeRun } from '../api/events'
import type { UsageReport } from '../api/types'
import type { RunPermission } from '../events/types'
import type { TimelineEvent, TimelineItem } from './timeline'
import { appendUser, applyEvent, subagentTag } from './timeline'

export type RunPhase = 'idle' | 'starting' | 'running' | 'settling' | 'error'

/** 合帧缓冲里「父运行」那一份的键：子运行的键是 `{task}\u0000{index}`。 */
const PARENT_KEY = ''

/** 一截待刷的流式增量：文本 + 首片到达的时间 + 它的来源（null = 父运行）。 */
type PendingDelta = { text: string; ts: number; tag: { task: string; index: number } | null }

/** 一条待决项：kind='approval' 等裁决、'question' 等回答（两者共用同一个界面槽）。 */
export type LiveApproval = {
  approvalId: string
  kind: 'approval' | 'question'
  tool: string
  arguments: string
  reason: string
  /** 选择题的选项（空 = 自由回答）。 */
  options: string[]
}

/** 事件里的 arguments 可能是字符串也可能是对象：都念成人看的样子，别显示 [object Object]。 */
function argumentsText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** 事件帧：只读这四处；`ts` 用来算思考段的时长。 */
type EventFrame = { type: string; ts: number; data?: Record<string, unknown> }

export type RunStream = ReturnType<typeof useRunStream>

export function useRunStream(sessionId: string | null, onSettled: () => void) {
  const [phase, setPhase] = useState<RunPhase>('idle')
  const [runId, setRunId] = useState<string | null>(null)
  const [items, setItems] = useState<TimelineItem[]>([])
  // items 属于哪个会话：切走会话后它的段落不跟过去，切回来时按 entry_id 去重后重并。
  const [attachedSession, setAttachedSession] = useState<string | null>(null)
  const [approvals, setApprovals] = useState<LiveApproval[]>([])
  // 每轮的用量快照（run_status 带，run_finished 再兜一次）：上下文环要吃它才"动态"——
  // 落盘的那份只在本轮结束时刷新，运行中会一直停在上一轮。
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 内核在 run_started 里记录的实际权限形态（normal/full）；null = 还没有这个事实。
  const [runPermission, setRunPermission] = useState<RunPermission | null>(null)

  const runIdRef = useRef<string | null>(null)
  const sessionRef = useRef<string | null>(null)
  sessionRef.current = attachedSession
  const subRef = useRef<{ abort: () => void } | null>(null)
  const settlingRef = useRef(false)
  // delta 合帧：增量文本先进 ref，rAF 每帧至多刷一次 state——流式重渲染从
  // 「每 token 两次」（正文/思考各一次）降到「每帧至多一次」，
  // Markdown 的重解析随之合帧（它是按文本记忆化的，state 不变就不重算）。
  // 哨兵用独立布尔而非帧句柄：句柄赋值发生在 rAF 注册之后，同步执行的
  // 测试桩会把「已消费」的句柄覆盖回非空，卡死后续所有增量。
  // 后台标签页 rAF 停发，另挂 1s 定时器兜底：增量最迟一秒落 state，
  // 不丢不重，也不会在隐藏页里无界积压（评审 L5）。
  //
  // 合帧**按来源分组**（阶段 53：子运行也流式，两边的增量会交错到达）：
  // 父运行的正文与每条子运行的正文各自成串——一面之词混进另一条时间线，
  // 就是「子 agent 说的话算在主 agent 头上」这种脏数据。
  const pendingFrameRef = useRef(false)
  const frameHandleRef = useRef<number | null>(null)
  const fallbackTimerRef = useRef<number | null>(null)
  const pendingRef = useRef(new Map<string, PendingDelta>())
  const flushDeltas = useCallback(() => {
    pendingFrameRef.current = false
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current)
      fallbackTimerRef.current = null
    }
    if (pendingRef.current.size === 0) return
    const chunks = [...pendingRef.current.values()]
    pendingRef.current.clear()
    setItems((cur) =>
      chunks.reduce((acc, chunk) => {
        const event: TimelineEvent = {
          type: 'assistant_delta',
          ts: chunk.ts,
          data: chunk.tag === null ? { text: chunk.text } : { text: chunk.text, subagent: chunk.tag },
        }
        return applyEvent(acc, event)
      }, cur),
    )
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
  /** 任一来源的未刷帧增量作废（终态 / 重置）。 */
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
    pendingRef.current.clear()
  }, [])
  /** 只作废**父运行**的未刷帧增量：父的持久消息到了，它那截流式残段就没有意义了；
   *  子运行的增量还在路上，不能被一起丢掉。 */
  const discardParentDeltas = useCallback(() => {
    pendingRef.current.delete(PARENT_KEY)
  }, [])
  useEffect(() => () => discardPendingDeltas(), [discardPendingDeltas])

  /**
   * 收尾：收订阅、清运行态，**保留 items**（时间线不跳变）；下次发送或切会话再收拾。
   *
   * `error` 不在这里清：运行失败（run_failed）的原因是这个运行唯一的痕迹——清掉它，
   * 界面就只剩「用户那句话 + 什么都没发生」，那正是「run 突然停了」的观感。
   * 它留到下一次 `send`（新一次运行）或 `attach`（换一条流）时才清。
   */
  const settle = useCallback(() => {
    subRef.current?.abort()
    subRef.current = null
    runIdRef.current = null
    settlingRef.current = false
    discardPendingDeltas()
    setRunId(null)
    setApprovals([])
    setPhase('idle')
  }, [discardPendingDeltas])

  /** 事件里带的用量快照：形状由内核保证（usage_report 一个出口），这里只挡非对象。
   *  带 subagent 标记的事件是**子运行**的读数（它就是同一个事件名 + 标记）——子运行的
   *  上下文占用与父运行不是一回事，拿它顶替父读数会让容量环跳来跳去。 */
  const takeUsage = (data: Record<string, unknown>) => {
    if (data.subagent !== undefined) return
    const report = data.usage
    if (report !== null && typeof report === 'object') setUsage(report as UsageReport)
  }

  const handleEvent = useCallback(
    (e: EventFrame) => {
      const data = (e.data ?? {}) as Record<string, unknown>
      switch (e.type) {
        case 'run_started': {
          // 两值口径：normal = 默认形态（毁灭级命令问一次）；full = 完全访问。
          // 沙箱事实在 sandbox_state/sandbox_notes 里，界面暂不展示，只留权限这一条。
          if (data.permission === 'normal' || data.permission === 'full') {
            setRunPermission(data.permission)
          }
          return
        }
        case 'assistant_delta': {
          const text = data.text
          if (typeof text === 'string' && text) {
            const tag = subagentTag(data)
            const key = tag === null ? PARENT_KEY : `${tag.task}\u0000${tag.index}`
            const pending = pendingRef.current.get(key)
            pendingRef.current.set(key, { text: (pending?.text ?? '') + text, ts: pending?.ts ?? e.ts, tag })
            scheduleDeltaFlush()
          }
          return
        }
        case 'assistant_message': {
          // 父运行的持久消息取代增量累积（重放时 delta 已丢失，durable 是权威）；
          // 子运行的增量不受影响——它们不属于这一条消息。
          discardParentDeltas()
          setItems((cur) => applyEvent(cur, { type: e.type, ts: e.ts, data }))
          return
        }
        case 'approval_requested': {
          setApprovals((a) => [
            ...a,
            {
              approvalId: String(data.approval_id ?? ''),
              kind: data.kind === 'question' ? 'question' : 'approval',
              tool: String(data.tool ?? ''),
              arguments: argumentsText(data.arguments),
              reason: String(data.reason ?? ''),
              options: Array.isArray(data.options) ? data.options.map((item) => String(item)) : [],
            },
          ])
          return
        }
        case 'approval_resolved': {
          const id = String(data.approval_id ?? '')
          setApprovals((a) => a.filter((x) => x.approvalId !== id))
          return
        }
        case 'run_finished':
        case 'run_failed':
        case 'run_cancelled': {
          if (settlingRef.current) return
          settlingRef.current = true
          discardPendingDeltas()
          takeUsage(data)
          if (e.type === 'run_failed') {
            setError(String(data.message ?? '运行失败'))
            // 失败段进时间线（带 entry_id，与重读会话后的那条对齐）；外面那行提示留一份，
            // 覆盖"这条记账还没落盘"的窗口（事件与条目之间只差一次写盘）
            setItems((cur) => applyEvent(cur, { type: e.type, ts: e.ts, data }))
          }
          setPhase('settling')
          onSettled()
          return
        }
        default: {
          // 其余全交给归并：不相关的事件类型是空操作，返回同一个数组引用，
          // React 也不会因为一次无关事件重渲染。带 usage 的事件（run_status）顺手收下。
          takeUsage(data)
          setItems((cur) => applyEvent(cur, { type: e.type, ts: e.ts, data }))
        }
      }
    },
    [discardParentDeltas, discardPendingDeltas, onSettled, scheduleDeltaFlush],
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
    async (
      prompt: string,
      full: boolean,
      model?: string | null,
      branch?: string,
      effort?: string | null,
    ) => {
      if (!sessionId) return
      setPhase('starting')
      setError(null)
      discardPendingDeltas()
      // 换会话就另起一条：上一条会话的段落不跟着走。
      setItems((cur) => (sessionRef.current === sessionId ? appendUser(cur, prompt) : appendUser([], prompt)))
      setAttachedSession(sessionId)
      setApprovals([])
      setRunPermission(null)
      try {
        const created = await startRun(sessionId, {
          prompt,
          // 只在选了覆盖时才带 model：不带 = 服务端按设置解析（与旧行为逐字一致）
          ...(model ? { model } : {}),
          // 同理只在选了档位时才带：不带 = 这次请求不发这个参数
          ...(effort ? { reasoning_effort: effort } : {}),
          // 只在非主线时才带 branch：不带 = 服务端默认 main，载荷与旧行为逐字一致
          ...(branch && branch !== 'main' ? { branch } : {}),
          // 完全访问的唯一凭据；默认形态不带这个字段。
          ...(full ? { full_access_ack: true } : {}),
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
      settle()
      setError(null) // 换一条流：上一条的失败不再挂在界面上
      setItems([])
      setAttachedSession(sessionId)
      runIdRef.current = id
      setRunId(id)
      setPhase('running')
      subscribe(id)
    },
    [sessionId, settle, subscribe],
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

  /** 回答一次提问：与裁决共用端点，载荷换 answer。 */
  const answer = useCallback(async (approvalId: string, text: string) => {
    const id = runIdRef.current
    if (!id) return
    await answerApproval(id, approvalId, text).catch(() => {})
  }, [])

  return {
    phase,
    runId,
    items,
    attachedSession,
    approvals,
    runPermission,
    usage,
    error,
    send,
    stop,
    decide,
    answer,
    attach,
    settle,
  }
}
