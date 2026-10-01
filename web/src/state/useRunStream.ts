/**
 * 一次运行的流式视图：SSE → `reducer` → 界面。
 *
 * **为什么整页按 `key` 重建而不是写状态清理逻辑**：切换会话时，上一轮运行的流式
 * 累积、待决审批、错误都必须清空。用 `useEffect` 逐项 reset 会漏（漏一项就出现
 * "新会话里挂着旧会话的审批条"），而 `App` 给页组件挂 `key={sessionId}` 让 React
 * 直接卸载重建，残留状态**在类型上就不可能存在**。代价是切回来不保留滚动位置，
 * 可接受（会话本来就要重新拉条目）。
 *
 * 三种来源要分清，混在一起会让"历史为什么不完整"变成不可诊断的问题：
 *   · 落盘条目（`/entries`）—— 历史真相，来自 REST；
 *   · 实时事件（SSE）—— 增量；
 *   · 合流 —— 本 hook。刷新与重连后**以落盘条目为准**，实时事件只追加在其后。
 *
 * 视图 reducer 拥有两个动作：`event`（内核事件）与 `rebuilt`（用落盘条目重建）。
 * 把后者做成本文件的动作类型、而不是伪造一个内核事件，是为了不让"渲染期事实"和
 * "线格式事件"共用一条通路——那正是上一版前端出过的错。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { answerApproval, cancelRun, fetchEntries, startRun } from '../api/sessions'
import { subscribeRunEvents } from '../api/stream'
import type { ApiError } from '../api/client'
import type { Entry, PermissionMode } from '../api/types'
import type { EventEnvelope } from '../events/types'
import { buildStartRunInput } from '../features/composer/lib/permission'
import { applyEvent, emptyView, viewFromEntries } from '../events/reducer'
import type { RunView } from '../events/reducer'

/** 单页拉多少条落盘条目。500 够一屏历史；再多只是把首屏拖慢。 */
export const ENTRY_PAGE_LIMIT = 500

type ViewAction =
  | { type: 'event'; event: EventEnvelope }
  | { type: 'rebuilt'; entries: Entry[]; runId: string | null }

function viewReducer(view: RunView, action: ViewAction): RunView {
  switch (action.type) {
    case 'event':
      return applyEvent(view, action.event)
    case 'rebuilt':
      /*
       * 重建保留 `sessionId` 与 `runId`：它们不属于条目，而是"这片视图在看什么"。
       * 丢掉 `runId` 会让正在进行的运行在刷新后与它的 SSE 流失去关联。
       */
      return viewFromEntries({ ...emptyView(view.sessionId), runId: action.runId }, action.entries)
  }
}

export interface UseRunStreamInput {
  sessionId: string | null
  branch: string
}

export interface UseRunStreamResult {
  view: RunView
  /** 本层的错误（拉条目 / 起运行 / 订阅失败）；**运行自身**的失败在 `view.error`。 */
  error: string | null
  /** 正在起运行（提交与后端开线程之间）。 */
  submitting: boolean
  /** SSE 已降级（重试耗尽）：界面据此说明"实时更新已停"，而不是假装还在流。 */
  degraded: boolean
  submit: (input: { prompt: string; permission: PermissionMode; fullAck?: boolean }) => Promise<void>
  cancel: () => Promise<void>
  answer: (approvalId: string, decision: 'allow' | 'deny') => void
  reload: () => Promise<void>
}

export function useRunStream({ sessionId, branch }: UseRunStreamInput): UseRunStreamResult {
  const [view, dispatch] = useReducer(viewReducer, sessionId, emptyView)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [degraded, setDegraded] = useState(false)

  /*
   * `runId` 必须是**状态**而不是从 `view` 里读：订阅的 effect 依赖它，
   * 而 `view.runId` 由 `run_started` 事件或起运行的返回值写入。
   */
  const [runId, setRunId] = useState<string | null>(null)

  /*
   * 实时 delta 的合流点：SSE 一个 token 一条帧，直接 dispatch 会让整棵树每 token
   * 重渲染一次。这里把同一微任务内的多条事件合并成一次 dispatch。
   *
   * 用 `queueMicrotask` 而不是 `requestAnimationFrame`：jsdom 没有可靠的 rAF 实现，
   * 而微任务合流在 60fps 下的效果已经足够（一帧内最多一次）。
   *
   * 顺序保证：批内按到达顺序折叠，与逐条 dispatch **完全等价**——这是"可以合并"
   * 的前提，也是 `applyEvent` 必须写成纯函数的原因。
   */
  const pending = useRef<EventEnvelope[]>([])
  const scheduled = useRef(false)

  const push = useCallback((event: EventEnvelope): void => {
    pending.current.push(event)
    if (scheduled.current) return
    scheduled.current = true
    queueMicrotask(() => {
      scheduled.current = false
      const batch = pending.current
      pending.current = []
      for (const item of batch) dispatch({ type: 'event', event: item })
    })
  }, [])

  /** 拉落盘条目并重建视图。刷新、重连补齐、切换分支共用这一条路径。 */
  const reload = useCallback(async (): Promise<void> => {
    if (sessionId === null) return
    try {
      const page = await fetchEntries(sessionId, {
        branch,
        order: 'asc',
        limit: ENTRY_PAGE_LIMIT,
      })
      dispatch({ type: 'rebuilt', entries: page.entries, runId })
      setError(null)
      /*
       * `truncated_tail` 是服务端明确的事实：尾部被截断过。
       * 不静默——它决定用户看到的"最后一条消息"是不是真的最后一条。
       */
      if (page.truncated_tail) {
        setError('会话尾部曾被截断（truncated_tail），历史可能不完整')
      }
    } catch (caught) {
      setError(describeError(caught))
    }
  }, [sessionId, branch, runId])

  /*
   * 首屏与换分支时重建。依赖 `branch` 而不是 `view`：重建是"重放历史"，
   * 不该被实时事件触发（那会把刚落进来的 delta 冲掉）。
   */
  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, branch])

  /*
   * 订阅事件流。依赖 `runId`：没有运行就没有流，`runId` 一变就换一条订阅，
   * 旧的被 abort（否则两个运行的 delta 会叠在同一份视图里）。
   */
  useEffect(() => {
    if (runId === null) return undefined
    const controller = new AbortController()

    const pump = async (): Promise<void> => {
      try {
        for await (const signal of subscribeRunEvents(runId, {
          signal: controller.signal,
          // delta 显式订阅：不订就没有逐字输出，只有整段落地。
          deltas: true,
          // `after` 用当前已知的最大 seq，重连从断点续，不重放整段历史。
          after: 0,
        })) {
          if (signal.kind === 'event') {
            push(signal.event)
          } else if (signal.kind === 'resync') {
            /*
             * 解析失败或缺口：**重新拉落盘条目**，而不是继续猜测。
             * 这正是 durable 事件的用途——落盘真相永远比拼凑的事件流可靠。
             */
            await reload()
          } else if (signal.kind === 'degraded') {
            setDegraded(true)
            setError(`实时事件流已降级：${signal.reason}`)
          }
          // `reconnect` 只是告知，界面不必为一次退避重连刷提示（会闪）。
        }
      } catch (caught) {
        if (controller.signal.aborted) return
        setError(describeError(caught))
      }
    }

    void pump()
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  /** 运行结束时刷新：终态事件的 entries 可能还没落盘，重拉一次让历史与视图对齐。 */
  useEffect(() => {
    if (view.phase === 'finished' || view.phase === 'failed' || view.phase === 'cancelled') {
      void reload()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.phase])

  const submit = useCallback(
    async ({
      prompt,
      permission,
      fullAck,
    }: {
      prompt: string
      permission: PermissionMode
      fullAck?: boolean
    }): Promise<void> => {
      if (sessionId === null) return
      setSubmitting(true)
      try {
        /*
         * 请求体由 `buildStartRunInput` **单点组装**，不在这里内联 `full_access_ack`。
         *
         * 为什么必须单点：`full` 会关掉沙箱与出网边界，它的授权凭据是安全关键规则，
         * 一旦有两份实现，改一处就会漏另一处。尤其是"**只在显式 ack 时才写 ack 键**"
         * 这条语义——内联写法很容易退化成"选了 full 就带上 ack"，那就等于把
         * 一次有意识的授权动作降级成了选菜单里的第三项，而这个动作正是服务端
         * 用 422 挡住不经思考的 `full` 的理由。
         *
         * `fullAck` 由输入区在用户**确认过那个弹窗之后**传入（见 Composer 的
         * full 确认流程），不是从 `permission` 反推出来的。
         */
        const created = await startRun(
          sessionId,
          buildStartRunInput({ prompt, mode: permission, branch, ...(fullAck === true ? { fullAck: true } : {}) }),
        )
        setRunId(created.run_id)
        setError(null)
      } catch (caught) {
        setError(describeError(caught))
        // 起运行失败**必须往上抛**：输入区据此决定要不要保留草稿。
        throw caught
      } finally {
        setSubmitting(false)
      }
    },
    [sessionId, branch],
  )

  const cancel = useCallback(async (): Promise<void> => {
    if (runId === null) return
    try {
      await cancelRun(runId)
      // 取消是"请求"：真正的终态由 `run_cancelled` 事件到达，界面不自己改 phase。
      setError(null)
    } catch (caught) {
      setError(describeError(caught))
    }
  }, [runId])

  const answer = useCallback(
    (approvalId: string, decision: 'allow' | 'deny'): void => {
      if (runId === null) return
      void answerApproval(runId, approvalId, decision).catch((caught: unknown) => {
        /*
         * 重复答复服务端返回 200 `{accepted:false}`，不是错误；真正的错误
         * （404 / 409 已决 / 410 已过期）必须显示出来——否则用户会一直重复点
         * 一个已经失效的审批。
         */
        setError(describeError(caught))
      })
    },
    [runId],
  )

  return { view, error, submitting, degraded, submit, cancel, answer, reload }
}

/** `ApiError` 之外的异常不吞原因：网络层与运行时错误都要能看见原文。 */
function describeError(error: unknown): string {
  const apiError = error as Partial<ApiError>
  if (typeof apiError?.code === 'string') {
    return apiError.code === 'network_error'
      ? '连不上本地服务'
      : `${apiError.message ?? '请求失败'}（${apiError.code}）`
  }
  return error instanceof Error ? error.message : String(error)
}
