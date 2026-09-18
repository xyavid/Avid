/**
 * i18n：单文件字典 + `LocaleProvider` + `t(a.b)`（照 purrcat 的结构，只出中文）。
 *
 * 两条与 purrcat 的差别（它踩过的坑）：
 *   · 默认语言就是 `zh-CN`，且 index.html 的 lang 与之一致——否则首屏会闪一次语言
 *     切换、读屏器先按错的语言发音；
 *   · 回落到 key 本身时**开发模式报错**，避免漏翻只在用户眼前暴露。
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { DEFAULT_LOCALE, DICTIONARY } from './dictionary'
import type { Locale } from './dictionary'

const STORAGE_KEY = 'avid-locale'

export type TranslateVars = Record<string, string | number>

function interpolate(text: string, vars?: TranslateVars): string {
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  )
}

/** 字典里查一次；缺失时回落 key 并在开发模式报错。 */
export function translate(locale: Locale, key: string, vars?: TranslateVars): string {
  const table = DICTIONARY[locale] ?? DICTIONARY[DEFAULT_LOCALE]
  const text = table[key] ?? DICTIONARY[DEFAULT_LOCALE][key]
  if (text === undefined) {
    if (import.meta.env.DEV) {
      console.error(`[i18n] 缺少词条：${key}`)
    }
    return key
  }
  return interpolate(text, vars)
}

export interface LocaleApi {
  locale: Locale
  t: (key: string, vars?: TranslateVars) => string
  setLocale: (locale: Locale) => void
}

const LocaleContext = createContext<LocaleApi>({
  locale: DEFAULT_LOCALE,
  t: (key) => translate(DEFAULT_LOCALE, key),
  setLocale: () => undefined,
})

export function useTranslation(): LocaleApi {
  return useContext(LocaleContext)
}

function storedLocale(): Locale {
  if (typeof localStorage === 'undefined') return DEFAULT_LOCALE
  const saved = localStorage.getItem(STORAGE_KEY)
  return saved && saved in DICTIONARY ? (saved as Locale) : DEFAULT_LOCALE
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(storedLocale)

  useEffect(() => {
    document.documentElement.lang = locale
    if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, locale)
  }, [locale])

  const t = useCallback((key: string, vars?: TranslateVars) => translate(locale, key, vars), [locale])
  const setLocale = useCallback((next: Locale) => setLocaleState(next), [])
  const api = useMemo(() => ({ locale, t, setLocale }), [locale, t, setLocale])

  return <LocaleContext.Provider value={api}>{children}</LocaleContext.Provider>
}
