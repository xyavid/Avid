/**
 * 活动域 store：唯一写入者是 `events/reducer.ts` 的纯函数与 `events/coalescer.ts`
 * 的提交回调（§3.4 规则 3）。
 *
 * 组件只读（`useRunView` / `useRunSelector`）；写入只能经过 `runStoreActions`，
 * 而它的唯一调用点是 `routes/`（L4：唯一允许把查询结果与 store 拼起来的地方）。
 * scripts/check-layers.mjs 会检查 import 面。
 */

import { create } from 'zustand'

import type { Entry, RunStatus } from '../api/types'
import type { EventEnvelope } from '../events/types'
import { applyDelta, applyEvent, emptyView, viewFromEntries } from '../events/reducer'
import type { RunPhase, RunView } from '../events/reducer'

export function phaseFromStatus(status: RunStatus): RunPhase {
  switch (status) {
    case 'running':
      return 'streaming'
    case 'awaiting_approval':
      return 'awaiting_approval'
    case 'finished':
      return 'done'
    case 'failed':
      return 'failed'
    case 'cancelled':
      return 'cancelled'
    default:
      return 'idle'
  }
}

interface RunStoreState {
  view: RunView
  apply: (event: EventEnvelope) => void
  commitDelta: (text: string) => void
  rebuild: (entries: Entry[]) => void
  reset: (sessionId: string | null) => void
  setPhase: (phase: RunPhase) => void
  requestCancel: () => void
  markDetached: (value: boolean) => void
}

export const useRunStore = create<RunStoreState>((set) => ({
  view: emptyView(),
  apply: (event) => set((state) => ({ view: applyEvent(state.view, event) })),
  commitDelta: (text) => set((state) => ({ view: applyDelta(state.view, text) })),
  rebuild: (entries) => set((state) => ({ view: viewFromEntries(state.view, entries) })),
  reset: (sessionId) => set({ view: emptyView(sessionId) }),
  setPhase: (phase) => set((state) => ({ view: { ...state.view, phase } })),
  requestCancel: () =>
    set((state) => ({
      view: { ...state.view, phase: 'cancelling', cancelRequested: true },
    })),
  markDetached: (value) => set((state) => ({ view: { ...state.view, detached: value } })),
}))

/** 只读访问器：L2 features 只能用这两个。 */
export const useRunView = (): RunView => useRunStore((state) => state.view)

export const useRunSelector = <T,>(selector: (view: RunView) => T): T =>
  useRunStore((state) => selector(state.view))

/** 写入口：import 面由 check-layers 限制在 state/ / events/ / routes/ 之内。 */
export const runStoreActions = {
  apply: (event: EventEnvelope) => useRunStore.getState().apply(event),
  commitDelta: (text: string) => useRunStore.getState().commitDelta(text),
  rebuild: (entries: Entry[]) => useRunStore.getState().rebuild(entries),
  reset: (sessionId: string | null) => useRunStore.getState().reset(sessionId),
  setPhase: (phase: RunPhase) => useRunStore.getState().setPhase(phase),
  requestCancel: () => useRunStore.getState().requestCancel(),
  markDetached: (value: boolean) => useRunStore.getState().markDetached(value),
}
