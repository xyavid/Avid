/**
 * 三栏骨架（阶段 1 立，阶段 4 改插槽式）：只定宽高与栅格，对应报告 §6 的布局骨架：
 *
 *   顶栏 44px ｜ 侧栏 240px ｜ 对话列 ≤720px 居中（由表面自排）｜ 右栏 280px
 *
 * 宽高全部引用 tokens.css 的布局 token；插槽缺省时显示阶段 1 的验收标注。
 * 表面（surfaces/*）负责往插槽里填内容与自己的居中滚动结构。
 */

import type { ReactNode } from 'react'

import { useAutoHideScroll } from '../ui/useAutoHideScroll'

function RegionNote({ children }: { children: ReactNode }) {
  return <span className="font-mono text-micro text-ink-muted">{children}</span>
}

export type AppShellProps = {
  sidebar?: ReactNode
  main?: ReactNode
  rail?: ReactNode
  /** 顶栏右侧操作区（设置按钮等）；缺省显示占位标注。 */
  actions?: ReactNode
}

export function AppShell({ sidebar, main, rail, actions }: AppShellProps) {
  const railScrollRef = useAutoHideScroll<HTMLElement>()
  return (
    <div className="grid h-dvh grid-rows-[var(--titlebar-h)_minmax(0,1fr)] bg-paper font-ui text-ink">
      <header className="flex items-center justify-between border-b border-hair px-a16">
        <span className="font-serif text-title tracking-[0.01em]">Avid</span>
        {actions ?? <RegionNote>主题 · 状态占位</RegionNote>}
      </header>

      <div className="grid min-h-0 grid-cols-[var(--sidebar-width)_minmax(0,1fr)_var(--channel-inspector-width)]">
        <aside className="flex min-h-0 flex-col border-r border-hair bg-sidebar p-a12">
          {sidebar ?? <RegionNote>侧栏 240px · 会话列表占位</RegionNote>}
        </aside>

        <main className="min-h-0 overflow-hidden">{main ?? <RegionNote>对话列 ≤720px · 阶段 4 组装</RegionNote>}</main>

        <aside ref={railScrollRef} className="scroll-auto min-h-0 overflow-y-auto border-l border-hair p-a12">
          {rail ?? <RegionNote>右栏 280px · 上下文占位</RegionNote>}
        </aside>
      </div>
    </div>
  )
}
