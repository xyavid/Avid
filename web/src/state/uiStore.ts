/**
 * 界面域：只存**纯 UI 偏好**，持久化到 localStorage。
 *
 * 服务端状态不在本地留副本（反面样本 purrcat 把画布图既 persist 又存服务端，
 * 两份真相必然分叉）。这里的每一项都是「用户自己的偏好」，丢了不影响正确性。
 */

import { create } from 'zustand'

import type { Density } from '../lib/density'
import { persist } from 'zustand/middleware'

export type TextScale = 90 | 100 | 110 | 125
export type InspectorTab = 'content' | 'diff' | 'json'

export interface UiState {
  textScale: TextScale
  density: Density
  inspectorOpen: boolean
  inspectorTab: InspectorTab
  navCollapsed: boolean
  thinkingExpanded: boolean
  approvalsExpanded: boolean
  autoApprove: boolean
  taskFilter: 'all' | 'pending' | 'in_progress' | 'completed' | 'blocked'
  /** 未发送的草稿：刷新后能恢复（只是 UI 便利，不进会话）。 */
  draft: string
  draftSavedAt: number | null
  setTextScale: (value: TextScale) => void
  setDensity: (value: Density) => void
  toggleInspector: (open?: boolean) => void
  setInspectorTab: (tab: InspectorTab) => void
  toggleNav: (collapsed?: boolean) => void
  toggleThinking: (expanded?: boolean) => void
  toggleApprovals: (expanded?: boolean) => void
  setAutoApprove: (value: boolean) => void
  setTaskFilter: (value: UiState['taskFilter']) => void
  setDraft: (value: string) => void
  clearDraft: () => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      textScale: 100,
      density: 'comfy',
      inspectorOpen: false,
      inspectorTab: 'content',
      navCollapsed: false,
      thinkingExpanded: false,
      approvalsExpanded: true,
      autoApprove: false,
      taskFilter: 'all',
      draft: '',
      draftSavedAt: null,
      setTextScale: (value) => set({ textScale: value }),
      setDensity: (value) => set({ density: value }),
      toggleInspector: (open) => set((state) => ({ inspectorOpen: open ?? !state.inspectorOpen })),
      setInspectorTab: (tab) => set({ inspectorTab: tab, inspectorOpen: true }),
      toggleNav: (collapsed) => set((state) => ({ navCollapsed: collapsed ?? !state.navCollapsed })),
      toggleThinking: (expanded) =>
        set((state) => ({ thinkingExpanded: expanded ?? !state.thinkingExpanded })),
      toggleApprovals: (expanded) =>
        set((state) => ({ approvalsExpanded: expanded ?? !state.approvalsExpanded })),
      setAutoApprove: (value) => set({ autoApprove: value }),
      setTaskFilter: (value) => set({ taskFilter: value }),
      setDraft: (value) => set({ draft: value, draftSavedAt: Date.now() }),
      clearDraft: () => set({ draft: '', draftSavedAt: null }),
    }),
    { name: 'avid-ui', version: 1 },
  ),
)
