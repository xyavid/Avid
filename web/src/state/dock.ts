/**
 * 侧边 dock 的界面域状态（localStorage 持久化——界面域状态归前端的约定，
 * appearance.ts 同一模式）。只持久化开合与激活面板：宽度、面板内容不进存储。
 */

import { useCallback, useEffect, useState } from 'react'

export type DockPanelId = 'context' | 'processes' | 'review' | 'terminal' | 'browser'

const STORAGE_KEY = 'avid.dock'
const PANEL_IDS: DockPanelId[] = ['context', 'processes', 'review', 'terminal', 'browser']

type StoredDock = { open: boolean; active: DockPanelId }

function load(): StoredDock {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return { open: false, active: 'context' }
    const parsed = JSON.parse(raw) as Partial<StoredDock>
    return {
      open: parsed.open === true,
      active: PANEL_IDS.includes(parsed.active as DockPanelId)
        ? (parsed.active as DockPanelId)
        : 'context',
    }
  } catch {
    return { open: false, active: 'context' }
  }
}

export function useDock() {
  const [open, setOpen] = useState(() => load().open)
  const [active, setActive] = useState<DockPanelId>(() => load().active)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ open, active }))
  }, [open, active])

  const toggle = useCallback(() => setOpen((v) => !v), [])
  const close = useCallback(() => setOpen(false), [])
  const select = useCallback((id: DockPanelId) => {
    setActive(id)
    setOpen(true) // 点面板图标即展开：收起态下选面板是最自然的展开方式
  }, [])

  return { open, active, toggle, close, select }
}
