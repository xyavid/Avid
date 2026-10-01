/**
 * 滚动条自动隐藏（阶段 5 回补）：把 ref 挂到滚动容器上并配 `.scroll-auto` 类
 * （样式在 base.css）——滚动中加 `is-scrolling`，停滚 1 秒后移除，滚动条随之
 * 淡出；悬停的显现由 CSS :hover 负责。对话栏、项目栏、会话列表、右栏共用。
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
