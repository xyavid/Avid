/**
 * Conversation scrolling: auto-hiding scrollbar, bottom pinning, and the anchor protocol for
 * "load earlier" — content growth follows only within 32px of the bottom, a prepend sets
 * `holdRef.current` so the caller's layout effect owns anchor restoration, and `reset()`
 * restores the pin when switching sessions.
 * Scrolling assigns `scrollTop` directly rather than `scrollTo`: same behavior, jsdom-testable.
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

  /** Call on session switch/clear: pin back to default so the next content lands at the bottom. */
  const reset = useCallback(() => {
    pinnedRef.current = true
    setPinned(true)
  }, [])

  const handleScroll = useCallback(() => {
    const el = ref.current
    if (el === null) return
    // Scrollbar auto-hide, same discipline as useAutoHideScroll, merged here for this element.
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
    if (holdRef?.current) return // the prepending caller restores the anchor in its layout effect
    if (pinnedRef.current) el.scrollTop = el.scrollHeight
    // eslint-disable-next-line react-hooks/exhaustive-deps -- follow on content growth only
  }, [dep])

  useEffect(
    () => () => {
      if (idleTimer.current !== null) window.clearTimeout(idleTimer.current)
    },
    [],
  )

  return { ref, pinned, scrollToBottom, handleScroll, reset }
}
