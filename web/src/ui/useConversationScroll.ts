/**
 * 对话列滚动三合一：滚动条自动隐藏 + 贴底跟随 + 「加载更早」锚点。
 *
 * - 内容增长（dep 变化）且用户贴在底部（距底 < 32px）→ 自动跟到新底：聊天
 *   界面的默认预期；用户上滚即脱离跟随（pinned=false），界面据此显示跳底钮。
 * - holdRef 携带「加载更早」前的 {top, height}：prepend 后按差值回滚，视口
 *   钉在同一条旧消息上；贴底时这个回滚恰好等于跟到新底，两者不打架。
 * - 打开会话的首次内容就绪也走同一条路：初始 pinned=true，无条件落底——
 *   用户要看的是最新消息，不是最早那条。
 *
 * 滚动用 scrollTop 直接赋值而非 scrollTo：行为一致，且 jsdom 可测。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const NEAR_BOTTOM_PX = 32
const SCROLL_IDLE_MS = 1000

export type ScrollAnchor = { top: number; height: number }

export function useConversationScroll(dep: unknown, holdRef?: { current: ScrollAnchor | null }) {
  const ref = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const [pinned, setPinned] = useState(true)
  const idleTimer = useRef<number | null>(null)

  const scrollToBottom = useCallback(() => {
    const el = ref.current
    if (el === null) return
    pinnedRef.current = true
    setPinned(true)
    el.scrollTop = el.scrollHeight
  }, [])

  const handleScroll = useCallback(() => {
    const el = ref.current
    if (el === null) return
    // 滚动条自动隐藏（与 useAutoHideScroll 同纪律，这里是同一元素的两件事合一）
    el.classList.add('is-scrolling')
    if (idleTimer.current !== null) window.clearTimeout(idleTimer.current)
    idleTimer.current = window.setTimeout(() => {
      el.classList.remove('is-scrolling')
      idleTimer.current = null
    }, SCROLL_IDLE_MS)
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    pinnedRef.current = nearBottom
    setPinned(nearBottom)
  }, [])

  useEffect(() => {
    const el = ref.current
    if (el === null) return
    const hold = holdRef?.current ?? null
    if (hold && holdRef) {
      // prepend 锚点：视口钉回同一条消息（贴底时恰为新的底，与跟随一致）
      holdRef.current = null
      el.scrollTop = hold.top + (el.scrollHeight - hold.height)
      return
    }
    if (pinnedRef.current) el.scrollTop = el.scrollHeight
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只随内容增长而跟随
  }, [dep])

  useEffect(
    () => () => {
      if (idleTimer.current !== null) window.clearTimeout(idleTimer.current)
    },
    [],
  )

  return { ref, pinned, scrollToBottom, handleScroll }
}
