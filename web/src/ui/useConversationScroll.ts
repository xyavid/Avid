/**
 * 对话列滚动三合一：滚动条自动隐藏 + 贴底跟随 + 「加载更早」锚点。
 *
 * - 内容增长（dep 变化）且用户贴在底部（距底 < 32px）→ 自动跟到新底：聊天
 *   界面的默认预期；用户上滚即脱离跟随（pinned=false），界面据此显示跳底钮。
 * - 「加载更早」不走 dep 效果：调用方置 holdRef.current = true（prepend 前），
 *   在自己的 layout effect 里按锚点差值回滚——dep 效果看到 hold 就跳过跟随，
 *   避免与流式合帧的无关提交抢锚点（评审 L7）。
 * - 打开会话的首次内容就绪也走同一条路：初始 pinned=true，无条件落底——
 *   用户要看的是最新消息，不是最早那条。reset() 在切会话时调用，把贴底
 *   状态拨回默认（评审 L6：上一会话停在顶部时，下一会话要照常落底）。
 *
 * 滚动用 scrollTop 直接赋值而非 scrollTo：行为一致，且 jsdom 可测。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const NEAR_BOTTOM_PX = 32
const SCROLL_IDLE_MS = 1000

export function useConversationScroll(dep: unknown, holdRef?: { current: boolean }) {
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

  /** 切会话/清空内容时调用：贴底状态拨回默认，下次内容就绪无条件落底。 */
  const reset = useCallback(() => {
    pinnedRef.current = true
    setPinned(true)
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
    if (holdRef?.current) return // prepend 的锚点恢复由调用方的 layout effect 负责
    if (pinnedRef.current) el.scrollTop = el.scrollHeight
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只随内容增长而跟随
  }, [dep])

  useEffect(
    () => () => {
      if (idleTimer.current !== null) window.clearTimeout(idleTimer.current)
    },
    [],
  )

  return { ref, pinned, scrollToBottom, handleScroll, reset }
}
