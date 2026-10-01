/**
 * 外观配置（界面域状态，localStorage 持久化——架构约定「界面域状态归前端」）。
 *
 * 模式：light（暖纸）/ dark（青夜）/ auto（跟随系统，默认）。
 * 应用方式：`document.documentElement.dataset.theme`——tokens.css 是 :root 基线，
 * themes/*.css 用 [data-theme] 覆盖变量，组件零改动（换肤机制的兑现）。
 * main.tsx 在挂载前调 initAppearance()，避免先亮后暗的闪烁。
 */

import { useCallback, useEffect, useState } from 'react'

export type AppearanceMode = 'light' | 'dark' | 'auto'
export type ThemeId = 'warm-paper' | 'midnight'

const STORAGE_KEY = 'avid.appearance'

/**
 * 主题注册表：label 与预览色是**主题调色板的展示副本**（设置界面画色卡用）。
 * 色值出自报告 §3.2（暖纸 --bg/--accent）与 §4（青夜 bg/accent），
 * 主题真值在 styles/themes/*.css，改主题要同步这里的预览。
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
    // 存储不可用（隐私模式）不是错误：本次会话内生效即可
  }
}

function applyResolved(mode: AppearanceMode): void {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.dataset.theme = resolveTheme(mode, prefersDark)
}

/** 应用模式到 DOM（不写存储）。 */
export function applyMode(mode: AppearanceMode): void {
  applyResolved(mode)
}

/** 挂载前用存储值定主题（避免闪烁）。 */
export function initAppearance(): void {
  applyResolved(readMode())
}

/** React 绑定：mode 变化即应用到 DOM；auto 时跟随系统偏好变化。 */
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
