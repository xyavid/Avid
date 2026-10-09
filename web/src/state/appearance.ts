/**
 * Appearance (UI-domain state, persisted to localStorage under `avid.appearance`).
 * Modes: light (warm paper) / dark (midnight) / auto (follow system, default); applied by setting
 * `document.documentElement.dataset.theme`, which themes/*.css override against tokens.css :root.
 */

import { useCallback, useEffect, useState } from 'react'

export type AppearanceMode = 'light' | 'dark' | 'auto'
export type ThemeId = 'warm-paper' | 'midnight'

const STORAGE_KEY = 'avid.appearance'

/**
 * Theme registry: label and preview colors are display copies of the palette in
 * styles/themes/*.css — keep them in sync when a theme changes.
 */
export type ThemeEntry = { id: ThemeId; label: string; kind: 'light' | 'dark'; bg: string; accent: string }

export const THEMES: ThemeEntry[] = [
  { id: 'warm-paper', label: '暖纸', kind: 'light', bg: '#F8F4ED', accent: '#537D96' },
  { id: 'midnight', label: '青夜', kind: 'dark', bg: '#3B4A54', accent: '#C99AAF' },
]

export function resolveTheme(mode: AppearanceMode, prefersDark: boolean): ThemeId {
  if (mode === 'light') return 'warm-paper'
  if (mode === 'dark') return 'midnight'
  return prefersDark ? 'midnight' : 'warm-paper'
}

export function readMode(): AppearanceMode {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) return 'auto'
    const parsed: unknown = JSON.parse(raw)
    return parsed === 'light' || parsed === 'dark' || parsed === 'auto' ? parsed : 'auto'
  } catch {
    return 'auto'
  }
}

export function writeMode(mode: AppearanceMode): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(mode))
  } catch {
    // Storage unavailable (private mode) is not an error: applying it this session is enough
  }
}

function applyResolved(mode: AppearanceMode): void {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.dataset.theme = resolveTheme(mode, prefersDark)
}

/** Apply a mode to the DOM (does not write storage). */
export function applyMode(mode: AppearanceMode): void {
  applyResolved(mode)
}

/** Set the theme from storage before mount (avoids a flash). */
export function initAppearance(): void {
  applyResolved(readMode())
}

/** React binding: applies the mode to the DOM on change; auto follows system preference changes. */
export function useAppearance(): { mode: AppearanceMode; setMode: (mode: AppearanceMode) => void } {
  const [mode, setModeState] = useState<AppearanceMode>(readMode)

  useEffect(() => {
    applyMode(mode)
    if (mode !== 'auto') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => applyMode('auto')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [mode])

  const setMode = useCallback((next: AppearanceMode) => {
    writeMode(next)
    setModeState(next)
  }, [])

  return { mode, setMode }
}
