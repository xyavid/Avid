/**
 * 导航列的搜索态：开关、词、以及焦点那两条规矩。
 *
 * 焦点是这里唯一有判断的部分，也正是提出来单放的理由：
 *   · **打开就聚焦**——这一个动作的目的就是打字，让人再点一次输入框是多余的（下一帧，
 *     等输入框真的挂上去）；
 *   · **关闭就清词**——留着词的搜索框关掉再打开，用户看到的是一个把列表藏掉一半的过滤
 *     条件，而他以为是全部。
 *
 * 不持久化：刷新后还留着一个藏掉一半列表的过滤条件是坑不是贴心（服务端也没有全文检索，
 * 为一个过滤条件加接口不值当）。
 */
import { useRef, useState } from 'react'

export function useSessionSearch() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const input = useRef<HTMLInputElement>(null)

  const focus = () => {
    // 下一帧：这一次 setState 之后输入框才挂上去。
    window.setTimeout(() => input.current?.focus(), 0)
  }

  const toggle = () => {
    // 副作用写在 updater 里会在 StrictMode 下跑两遍（React 可能重复调用 updater），
    // 所以这里从当前值直接算下一个值。
    const next = !open
    setOpen(next)
    if (next) focus()
    else setQuery('')
  }

  const clear = () => {
    setQuery('')
    focus()
  }

  const close = () => {
    setQuery('')
    setOpen(false)
  }

  return { open, query, input, setQuery, toggle, clear, close }
}
