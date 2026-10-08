/**
 * 三栏骨架（阶段 1 立，阶段 4 改插槽式）：只定宽高与栅格，对应报告 §6 的布局骨架：
 *
 *   顶栏 44px ｜ 侧栏 240px ｜ 对话列 ≤720px 居中（由表面自排）｜ 右栏 280px
 *
 * 宽高全部引用 tokens.css 的布局 token；插槽缺省时显示阶段 1 的验收标注。
 * 表面（surfaces/*）负责往插槽里填内容与自己的居中滚动结构。
 */

import type { ReactNode } from 'react'

import { AvidMark } from '../ui/Mark'

function RegionNote({ children }: { children: ReactNode }) {
  return <span className="font-mono text-micro text-ink-muted">{children}</span>
}

export type AppShellProps = {
  sidebar?: ReactNode
  main?: ReactNode
  rail?: ReactNode
  /** 顶栏右侧操作区。不给就留白——设置入口已挪到侧栏底栏，顶栏只留标识与字标。 */
  actions?: ReactNode
}

export function AppShell({ sidebar, main, rail, actions }: AppShellProps) {
  return (
    <div className="grid h-dvh grid-rows-[var(--titlebar-h)_minmax(0,1fr)] bg-paper font-ui text-ink">
      <header className="flex items-center justify-between border-b border-hair px-a16">
        {/* 标识锁定组合：标记直接贴在顶栏纸面上（无圆托、无底板），与字标同日排。
            标记 22px 比字标字号略大，是为了和衬线字标的字高对齐——等号对齐会让标记偏小。 */}
        <span className="flex items-center gap-a8">
          <AvidMark size={22} />
          <span className="font-serif text-title tracking-[0.01em]">Avid</span>
        </span>
        {actions}
      </header>

      <div
        className="grid min-h-0 grid-cols-[var(--sidebar-width)_minmax(0,1fr)_var(--channel-inspector-width)]"
        style={rail ? undefined : { gridTemplateColumns: 'var(--sidebar-width) minmax(0, 1fr)' }}
      >
        <aside className="flex min-h-0 flex-col border-r border-hair bg-sidebar p-a12">
          {sidebar ?? <RegionNote>侧栏 240px · 会话列表占位</RegionNote>}
        </aside>

        <main className="min-h-0 overflow-hidden">{main ?? <RegionNote>对话列 ≤720px · 阶段 4 组装</RegionNote>}</main>

        {/* 右列只给栅格与滚动边界：边线、内边距、滚动条样式都由插槽内容自带
            （dock 是常驻面板，它的页签条要贴着列缘）。 */}
        {rail && <aside className="min-h-0 overflow-hidden">{rail}</aside>}
      </div>
    </div>
  )
}
