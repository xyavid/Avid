/**
 * Side column widths (UI-domain state, persisted to localStorage under `avid.columns`).
 * Storage keeps only the wanted widths; every render passes them through `clampWidths`: clamp to
 * each column's limits, then shrink the right column first and the left one second, never below
 * their minimums. Initial values come from the layout tokens (`--sidebar-width` /
 * `--channel-inspector-width`), falling back to numbers only when the styles are unreadable.
 */

import { useCallback, useEffect, useState } from 'react'

export type ColumnId = 'sidebar' | 'rail'

export type ColumnWidths = Record<ColumnId, number>

export const COLUMN_LIMITS: Record<ColumnId, { min: number; max: number }> = {
  sidebar: { min: 180, max: 420 },
  rail: { min: 220, max: 560 },
}

/** Minimum width of the conversation column: dragging must never squeeze it away. */
export const MAIN_MIN_WIDTH = 520

const STORAGE_KEY = 'avid.columns'
const TOKENS: Record<ColumnId, string> = {
  sidebar: '--sidebar-width',
  rail: '--channel-inspector-width',
}
const FALLBACK: ColumnWidths = { sidebar: 240, rail: 280 }

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.round(Math.min(max, Math.max(min, value)))
}

/** px value of a token like `--sidebar-width`; null when styles are not loaded (jsdom). */
function tokenWidth(name: string): number | null {
  if (typeof window === 'undefined') return null
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const parsed = Number.parseFloat(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

export function defaultWidths(): ColumnWidths {
  return {
    sidebar: tokenWidth(TOKENS.sidebar) ?? FALLBACK.sidebar,
    rail: tokenWidth(TOKENS.rail) ?? FALLBACK.rail,
  }
}

function limitTo(id: ColumnId, value: number): number {
  return clamp(value, COLUMN_LIMITS[id].min, COLUMN_LIMITS[id].max)
}

/** Clamp and yield: widths actually rendered this frame (`hasRail=false` takes no rail budget). */
export function clampWidths(widths: ColumnWidths, viewport: number, hasRail: boolean): ColumnWidths {
  const sidebar = limitTo('sidebar', widths.sidebar)
  const rail = limitTo('rail', widths.rail)
  if (!hasRail) return { sidebar, rail }

  const budget = viewport - MAIN_MIN_WIDTH
  const overflow = sidebar + rail - budget
  if (overflow <= 0) return { sidebar, rail }

  // Shrink the right column first (it carries status panels), then the left one.
  const railFloor = COLUMN_LIMITS.rail.min
  const railTake = Math.max(0, Math.min(rail - railFloor, overflow))
  const sidebarTake = Math.max(0, Math.min(sidebar - COLUMN_LIMITS.sidebar.min, overflow - railTake))
  return { sidebar: sidebar - sidebarTake, rail: rail - railTake }
}

function load(): ColumnWidths {
  const fallback = defaultWidths()
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return fallback
    const parsed = JSON.parse(raw) as Partial<ColumnWidths>
    return {
      sidebar: typeof parsed.sidebar === 'number' ? parsed.sidebar : fallback.sidebar,
      rail: typeof parsed.rail === 'number' ? parsed.rail : fallback.rail,
    }
  } catch {
    return fallback
  }
}

export function useColumns() {
  const [widths, setWidths] = useState<ColumnWidths>(() => load())

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widths))
  }, [widths])

  /** Persist after a drag or key tweak: clamped, so out-of-range values never reach storage. */
  const resize = useCallback((id: ColumnId, value: number) => {
    setWidths((current) => ({ ...current, [id]: limitTo(id, value) }))
  }, [])

  /** Double-clicking a column edge resets to the layout-token default. */
  const reset = useCallback((id: ColumnId) => {
    setWidths((current) => ({ ...current, [id]: defaultWidths()[id] }))
  }, [])
  return { widths, resize, reset }
}

/** Viewport width: yielding during a drag and clamping on resize require subscribing to resize. */
export function useViewportWidth(): number {
  const [width, setWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return width
}
