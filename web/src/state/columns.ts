/**
 * 两侧列宽（界面域状态，localStorage 持久化——appearance/dock 同一模式）。
 *
 * 宽度是用户拖出来的偏好，但**不是拖多宽就多宽**：对话列要留住最小可读宽度，
 * 所以存储里只记「想要多少」，渲染前一律过 `clampWidths`——先按各自上下限夹，
 * 窗口不够就先收右列、再收左列，各自不低于下限（下限优先，主列自己扛）。
 *
 * 初值取自布局 token（`--sidebar-width` / `--channel-inspector-width`）：数字只有
 * 一份来源，token 改了初值跟着改；读不到（jsdom、样式未加载）才回落到下面的数。
 */

import { useCallback, useEffect, useState } from 'react'

export type ColumnId = 'sidebar' | 'rail'

export type ColumnWidths = Record<ColumnId, number>

export const COLUMN_LIMITS: Record<ColumnId, { min: number; max: number }> = {
  sidebar: { min: 180, max: 420 },
  rail: { min: 220, max: 560 },
}

/** 对话列的最小宽度：两侧再怎么拖也不许把它挤没。 */
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

/** 解析 `--sidebar-width` 这类 token 的 px 值；样式没加载（jsdom）时给 null。 */
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

/** 夹取 + 让位：返回这一帧真正渲染的宽度（`hasRail=false` 时右列不占预算）。 */
export function clampWidths(widths: ColumnWidths, viewport: number, hasRail: boolean): ColumnWidths {
  const sidebar = limitTo('sidebar', widths.sidebar)
  const rail = limitTo('rail', widths.rail)
  if (!hasRail) return { sidebar, rail }

  const budget = viewport - MAIN_MIN_WIDTH
  const overflow = sidebar + rail - budget
  if (overflow <= 0) return { sidebar, rail }

  // 先收右列（它承载的是状态面板），收到底再收左列。
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

  /** 拖完（或键盘调完）落一次盘：夹到上下限内，越界值不进存储。 */
  const resize = useCallback((id: ColumnId, value: number) => {
    setWidths((current) => ({ ...current, [id]: limitTo(id, value) }))
  }, [])

  /** 双击列缘复位：回到布局 token 的初值。 */
  const reset = useCallback((id: ColumnId) => {
    setWidths((current) => ({ ...current, [id]: defaultWidths()[id] }))
  }, [])
  return { widths, resize, reset }
}

/** 视口宽度：拖动期间要让位、窗口拉伸时要跟着夹，所以得订阅 resize。 */
export function useViewportWidth(): number {
  const [width, setWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return width
}
