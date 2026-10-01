/**
 * 桌面端断点。**本次范围只保证桌面端**，不做移动端布局——
 * 所以这里只有"够不够宽"，没有"用什么形态替代"。
 *
 * 两个阈值：
 *   · 1180px：低于它就收不起检查器了（对话列 720 + 导航 240 + 内边距 ≈ 1040，
 *     再塞 380 的检查器必然挤压正文）。到这一档检查器改为**浮层**。
 *   · 900px：低于它连导航列都要让位，否则对话列会被压到 400px 以下，
 *     中文在 400px 里每行只有 20 来字，读完一句要来回扫视。
 *
 * 为什么用 `matchMedia` 而不是监听 `resize` + 比较 `innerWidth`：
 * `matchMedia` 的 `change` 只在**跨过阈值**时触发，不必在每次拖拽窗口时都跑一遍
 * 比较与重渲染；它也是浏览器原生能力，不需要节流。
 */

import { useEffect, useState } from 'react'

export const WIDE_QUERY = '(min-width: 1180px)'
export const ROOMY_QUERY = '(min-width: 900px)'

export interface Viewport {
  /** ≥1180px：检查器可以常驻在右侧。 */
  isWide: boolean
  /** ≥900px：导航列可以常驻。低于它导航列降级为抽屉。 */
  isRoomy: boolean
}

function subscribe(query: string, onChange: (matches: boolean) => void): () => void {
  const list = window.matchMedia(query)
  const handler = (event: MediaQueryListEvent) => onChange(event.matches)
  list.addEventListener('change', handler)
  return () => list.removeEventListener('change', handler)
}

export function useViewport(): Viewport {
  /*
   * 初始值直接由 `matchMedia(...).matches` 算出（惰性初始化），**不**依赖
   * "订阅回调里同步一次"。`StrictMode` 会把 effect 跑两遍：如果初始值只在回调里
   * 被写入，第二遍挂载时就不会再写一次，组件会一直停在 `false`——
   * 表现是桌面端首次渲染就收起了导航列。惰性初始化没有这个洞。
   */
  const [isWide, setIsWide] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia(WIDE_QUERY).matches,
  )
  const [isRoomy, setIsRoomy] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia(ROOMY_QUERY).matches,
  )

  useEffect(() => subscribe(WIDE_QUERY, setIsWide), [])
  useEffect(() => subscribe(ROOMY_QUERY, setIsRoomy), [])

  return { isWide, isRoomy }
}
