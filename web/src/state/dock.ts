/**
 * Right dock UI state (persisted to localStorage under `avid.dock.column`); only open/active are
 * stored, never width or panel content. Defaults to collapsed; a manual open shows the chooser
 * first, while selecting a panel programmatically enters it directly.
 */

import { useCallback, useEffect, useState } from 'react'

/** Panel ids; a stored value not in `PANEL_IDS` falls back to the default panel. */
export type DockPanelId = 'files' | 'subagents' | 'scratch' | 'terminal'

const STORAGE_KEY = 'avid.dock.column'
const PANEL_IDS: DockPanelId[] = ['files', 'subagents', 'scratch', 'terminal']

type StoredDock = { open: boolean; active: DockPanelId }

const DEFAULTS: StoredDock = { open: false, active: 'files' }

function load(): StoredDock {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return DEFAULTS
    const parsed = JSON.parse(raw) as Partial<StoredDock>
    return {
      open: parsed.open === true,
      active: PANEL_IDS.includes(parsed.active as DockPanelId)
        ? (parsed.active as DockPanelId)
        : DEFAULTS.active,
    }
  } catch {
    return DEFAULTS
  }
}

export function useDock() {
  const [open, setOpen] = useState(() => load().open)
  const [active, setActive] = useState<DockPanelId>(() => load().active)
  // Chooser (panel list): the first screen of a manual open, kept in memory only.
  const [choosing, setChoosing] = useState(false)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ open, active }))
  }, [open, active])

  const toggle = useCallback(() => {
    setOpen((v) => {
      if (!v) setChoosing(true) // manual open: show the chooser first
      return !v
    })
  }, [])
  const close = useCallback(() => setOpen(false), [])
  const choose = useCallback((on: boolean) => setChoosing(on), [])
  const select = useCallback((id: DockPanelId) => {
    setActive(id)
    setChoosing(false) // explicit intent: enter the panel directly
    setOpen(true)
  }, [])

  return { open, active, choosing, toggle, close, choose, select }
}
