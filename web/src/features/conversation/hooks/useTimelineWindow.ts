import { useCallback, useLayoutEffect, useRef, useState } from 'react'

/** 一组 20 条：首屏只渲染尾部，顶部一个「加载更早」。 */
export const WINDOW_GROUP_SIZE = 20
/** 离开顶部多远才重新武装「碰顶加载」。 */
export const TOP_REARM_PX = 150
/** 一次加载后的冷却：惯性滚动不会连锁触发多次加载。 */
export const COOLDOWN_MS = 400
/** 贴底阈值。 */
export const STICK_PX = 50

export interface TimelineWindow {
  total: number
  visibleCount: number
  hasEarlier: boolean
  loadEarlier: () => void
  handleScroll: () => void
  containerRef: React.RefObject<HTMLDivElement>
  atBottom: boolean
  scrollToBottom: (behavior?: ScrollBehavior) => void
  armed: boolean
}

/**
 * 分组窗口化 + 高度补偿 + 贴底。四个阈值都写在上面的常量里；
 * 这是「先不做虚拟列表」的落地方式（1000 条量级先测它是否够）。
 */
export function useTimelineWindow(total: number, resetKey: string): TimelineWindow {
  const containerRef = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(WINDOW_GROUP_SIZE)
  const [atBottom, setAtBottom] = useState(true)
  const armed = useRef(true)
  const cooldownUntil = useRef(0)
  const stick = useRef(true)
  const pendingHeight = useRef<number | null>(null)

  const visible = Math.min(Math.max(visibleCount, WINDOW_GROUP_SIZE), Math.max(total, 1))

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    const element = containerRef.current
    if (!element) return
    element.scrollTo({ top: element.scrollHeight, behavior })
    stick.current = true
    setAtBottom(true)
  }, [])

  const loadEarlier = useCallback(() => {
    const element = containerRef.current
    if (element) pendingHeight.current = element.scrollHeight
    armed.current = false
    cooldownUntil.current = Date.now() + COOLDOWN_MS
    setVisibleCount((count) => count + WINDOW_GROUP_SIZE)
  }, [])

  const handleScroll = useCallback(() => {
    const element = containerRef.current
    if (!element) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    stick.current = distance <= STICK_PX
    setAtBottom(stick.current)
    if (element.scrollTop > TOP_REARM_PX && Date.now() > cooldownUntil.current) {
      armed.current = true
    }
    if (element.scrollTop <= 0 && armed.current && total > visible) loadEarlier()
  }, [loadEarlier, total, visible])

  // 插入历史后补偿高度：把视口钉在原位置，内容不跳动。
  useLayoutEffect(() => {
    const element = containerRef.current
    if (!element || pendingHeight.current === null) return
    const delta = element.scrollHeight - pendingHeight.current
    if (delta > 0) element.scrollTop += delta
    pendingHeight.current = null
  }, [visibleCount])

  // 新内容到达时：在底部才继续贴底，否则不抢用户的滚动。
  useLayoutEffect(() => {
    if (stick.current) scrollToBottom()
  }, [total, scrollToBottom])

  // 切换会话：瞬时贴底。
  useLayoutEffect(() => {
    setVisibleCount(WINDOW_GROUP_SIZE)
    scrollToBottom()
  }, [resetKey, scrollToBottom])

  return {
    total,
    visibleCount: visible,
    hasEarlier: total > visible,
    loadEarlier,
    handleScroll,
    containerRef,
    atBottom,
    scrollToBottom,
    armed: armed.current,
  }
}
