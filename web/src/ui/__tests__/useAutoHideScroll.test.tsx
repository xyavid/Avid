// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAutoHideScroll } from '../useAutoHideScroll'

function Probe() {
  const ref = useAutoHideScroll<HTMLDivElement>()
  return <div ref={ref} data-testid="scroller" />
}

function mount() {
  const view = render(<Probe />)
  const el = view.getByTestId('scroller')
  return { el, ...view }
}

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('useAutoHideScroll（滚动条自动隐藏）', () => {
  it('滚动时加 is-scrolling，停滚 1 秒后移除', () => {
    const { el } = mount()

    act(() => {
      el.dispatchEvent(new Event('scroll'))
    })
    expect(el.classList.contains('is-scrolling')).toBe(true)

    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(el.classList.contains('is-scrolling')).toBe(false)
  })

  it('连续滚动会重置计时（停止后才开始淡出）', () => {
    const { el } = mount()

    act(() => {
      el.dispatchEvent(new Event('scroll'))
      vi.advanceTimersByTime(700)
      el.dispatchEvent(new Event('scroll'))
      vi.advanceTimersByTime(700)
    })
    expect(el.classList.contains('is-scrolling')).toBe(true)

    act(() => {
      vi.advanceTimersByTime(400)
    })
    expect(el.classList.contains('is-scrolling')).toBe(false)
  })

  it('卸载后监听与计时都清掉，不再触碰节点', () => {
    const { el, unmount } = mount()
    unmount()

    act(() => {
      el.dispatchEvent(new Event('scroll'))
      vi.advanceTimersByTime(2000)
    })
    expect(el.classList.contains('is-scrolling')).toBe(false)
  })
})
