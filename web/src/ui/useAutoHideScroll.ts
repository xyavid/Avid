/**
 * Auto-hiding scrollbar: attach the ref to a `.scroll-auto` container (styles in base.css);
 * `is-scrolling` is added while scrolling and removed after 1s idle, fading the bar out —
 * the hover reveal stays in CSS.
 */

import { useEffect, useRef } from 'react'

export function useAutoHideScroll<T extends HTMLElement>(idleMs = 1000) {
  const ref = useRef<T>(null)

  useEffect(() => {
    const el = ref.current
    if (el === null) return
    let timer: number | null = null
    const onScroll = () => {
      el.classList.add('is-scrolling')
      if (timer !== null) window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        el.classList.remove('is-scrolling')
        timer = null
      }, idleMs)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [idleMs])

  return ref
}
