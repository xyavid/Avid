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
  /**
   * 页面背景的插画（data URL；null = 用纯 CSS 光斑）。
   *
   * 为什么存在界面域而不是服务端：它是**用户自己的观感偏好**，丢了不影响正确性——与
   * `textScale` / `density` 同一类。而且参照物的整幅插画不进仓（`budget.json` 的
   * `texture_bytes` 仍是 0），所以「哪张图」只能由用户在本机给。
   *
   * 这是阶段 23b 唯一一处**越过「承重层零改动」边界**的改动，原因与取舍记录在设计文档
   * §8.9 的预算条目里：入口本身必须有个地方持久化，而界面域正是为此存在的。
   */
  backdropArt: string | null
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
  setBackdropArt: (value: string | null) => void
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
      backdropArt: null,
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
      setBackdropArt: (value) => set({ backdropArt: value }),
    }),
    { name: 'avid-ui', version: 1 },
  ),
)
