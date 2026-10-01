// @vitest-environment jsdom
/**
 * AppShell 的**高度链**契约用例。
 *
 * 为什么单独给外壳写用例：加顶部条最容易改坏的不是"顶部条长什么样"，而是
 * `h-dvh`（根）→ 顶部条 `shrink-0` → 三栏容器 `flex-1 min-h-0` → 中栏
 * `min-h-0` 这条链。链一断，表现是"整页出现滚动条、输入区被推出视口"，
 * 而那要看真实视口高度才知道——jsdom 量不出布局，所以这里退一步：
 * 把链上每一环的**类名**钉住（与 SessionNav.test.tsx 同款做法），
 * 真实视口的行为由 e2e/layout.spec.ts 与 e2e/smoke.spec.ts 在 chromium 里量。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AppShell } from '../AppShell'

afterEach(cleanup)

function renderShell(overrides: Partial<Parameters<typeof AppShell>[0]> = {}) {
  return render(
    <AppShell
      nav={<span>左栏内容</span>}
      inspector={null}
      navCollapsed={false}
      inspectorInline={false}
      navInline
      {...overrides}
    >
      <span>中栏内容</span>
    </AppShell>,
  )
}

describe('AppShell 高度链', () => {
  it('根是 h-dvh，顶部条 shrink-0，三栏容器是 flex-1 min-h-0', () => {
    const { container } = renderShell()
    const root = container.firstElementChild as HTMLElement
    expect(root.className).toContain('h-dvh')
    expect(root.className).toContain('overflow-hidden')

    const header = root.querySelector('header')
    expect(header).not.toBeNull()
    // 44px 是 token `--avid-titlebar-h` 的值（恰好不在 Tailwind 标尺上，用任意值并注明）。
    expect(header?.className).toContain('h-[44px]')
    // shrink-0 是链上的关键一环：没有它顶部条会被三栏内容压扁。
    expect(header?.className).toContain('shrink-0')

    const columns = header?.nextElementSibling as HTMLElement
    expect(columns.className).toContain('flex-1')
    expect(columns.className).toContain('min-h-0')

    const main = columns.querySelector('main')
    expect(main?.getAttribute('id')).toBe('avid-main')
    expect(main?.className).toContain('min-h-0')
  })

  it('顶部条的三段插槽按位置渲染，内容由页面给', () => {
    renderShell({
      topbarLeft: <span>收起</span>,
      topbarCenter: <span>聊天 频道</span>,
      topbarRight: <span>窗口控制</span>,
    })

    const header = screen.getByText('聊天 频道').closest('header')
    expect(header).not.toBeNull()
    expect(header?.textContent).toContain('收起')
    expect(header?.textContent).toContain('窗口控制')
  })

  it('navInline=false 时不渲染左栏；inspectorInline=false 时检查器走浮层（在三栏容器之外）', () => {
    const { container } = renderShell({ navInline: false, inspector: <span>检查器</span> })

    expect(screen.queryByText('左栏内容')).toBeNull()
    const root = container.firstElementChild as HTMLElement
    const header = root.querySelector('header')
    const columns = header?.nextElementSibling as HTMLElement
    const overlay = screen.getByText('检查器')
    expect(columns.contains(overlay)).toBe(false)
    expect(root.contains(overlay)).toBe(true)
  })
})
