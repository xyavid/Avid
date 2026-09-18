import { useEffect, useState } from 'react'

/** 视口断点（设计文档 §8.6）：宽 ≥1280 / 中 960–1279 / 窄 <960。 */
export const MID_QUERY = '(min-width: 960px)'
export const WIDE_QUERY = '(min-width: 1280px)'

export interface Viewport {
  /** ≥1280：导航 320 + 对话卡 + 检查器 420。 */
  isWide: boolean
  /** ≥960：导航变图标轨，检查器变抽屉。 */
  isMid: boolean
}

/**
 * 断点判断用 `matchMedia` 而不是 Tailwind 的响应式类：三档之间的差异不只是宽度，
 * 还有「导航内容是否挂载」「检查器是列还是覆盖层」，那些是结构差异而不是样式差异。
 */
export function useViewport(): Viewport {
  const [state, setState] = useState({ isWide: false, isMid: false })

  useEffect(() => {
    const wide = window.matchMedia(WIDE_QUERY)
    const mid = window.matchMedia(MID_QUERY)
    const sync = () => setState({ isWide: wide.matches, isMid: mid.matches })
    sync()
    wide.addEventListener('change', sync)
    mid.addEventListener('change', sync)
    return () => {
      wide.removeEventListener('change', sync)
      mid.removeEventListener('change', sync)
    }
  }, [])

  return state
}
