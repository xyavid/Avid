/**
 * 应用外壳：**只做布局**。
 *
 * 边界写清楚：外壳不知道会话、不知道图标、不知道任何领域概念——它只把三栏摆好，
 * 并保证高度链正确。收起按钮、图标、文案都由各自的栏负责（导航列自己带收起按钮）。
 * 这样"外壳"在换页面结构时是唯一不用跟着改的文件。
 *
 * 高度链是本文件最容易出错的地方，说明一次：
 *   `h-dvh`（根）→ 各栏 `h-full min-h-0` → 消息区 `flex-1 min-h-0 overflow-y-auto`。
 * 少了任何一个 `min-h-0`，flex 子项的最小高度会等于内容高度，消息区就会把整页撑高、
 * 把输入区推出视口之外（表现为"整页有了滚动条，输入框看不见了"）。
 *
 * 宽度值 `w-60` / `w-12` 对应 token `--avid-nav-w`(240px) 与
 * `--avid-nav-w-collapsed`(48px)，恰好落在 Tailwind 标尺上，所以不写任意值。
 * 检查器的 380px（`--avid-inspector-w`）不在标尺上，用任意值并在此注明来源。
 */

import type { ReactNode } from 'react'

export interface AppShellProps {
  /** 左栏（会话导航）。 */
  nav: ReactNode
  /** 中栏（对话）。 */
  children: ReactNode
  /** 右栏（检查器）。null = 不渲染这一列。 */
  inspector: ReactNode | null
  navCollapsed: boolean
  /** ≥1180px 才让检查器占据一整列；否则它由调用方改成浮层。 */
  inspectorInline: boolean
  /** ≥900px 才让导航列常驻；否则它由调用方改成抽屉。 */
  navInline: boolean
}

export function AppShell({
  nav,
  children,
  inspector,
  navCollapsed,
  inspectorInline,
  navInline,
}: AppShellProps) {
  return (
    <div className="relative h-dvh w-full overflow-hidden bg-canvas">
      {/*
        纸面肌理：两层极淡的 radial-gradient，纯 CSS、零字节、无位图。
        铺在 `-z-10`——在内容之下、且不属于任何滚动容器，所以消息区滚动时纸面不动
        （纸在内容之下，而不是内容之上）。
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10"
        style={{ backgroundImage: 'var(--avid-paper-fiber)' }}
      />

      {/*
        跳到主内容：键盘用户的第一个 Tab 落点。`sr-only` + `focus:` 展开，不占布局。
        报告 §9 把"重投影/发光"列进黑名单，所以可见态也只有实底 + 发丝线，没有光晕。
      */}
      <a
        href="#avid-main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-a16 focus:top-a16 focus:z-tooltip focus:rounded-sm focus:border-hair focus:border-hair focus:bg-card focus:px-a12 focus:py-a6 focus:text-caption"
      >
        跳到主内容
      </a>

      <div className="flex h-full min-h-0 w-full">
        {/* 左栏：常驻或由调用方改成抽屉，这里只管占位与宽度。 */}
        {navInline ? (
          <div
            className={`h-full min-h-0 shrink-0 overflow-hidden border-r-hair bg-deep transition-[width] duration-slow ease-smooth ${
              navCollapsed ? 'w-12' : 'w-60'
            }`}
          >
            {nav}
          </div>        ) : null}

        {/* 中栏：对话。`min-w-0` 让内部的 truncate 生效（否则 flex 子项不肯收缩）。 */}
        <main id="avid-main" className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
          {children}
        </main>

        {/* 右栏：检查器常驻态。只有 `inspectorInline` 且确有选中项时才存在。 */}
        {inspectorInline && inspector !== null ? (
          <div className="h-full min-h-0 w-[380px] shrink-0 border-l-hair bg-card">
            {inspector}
          </div>
        ) : null}
      </div>

      {/* 检查器的浮层形态：宽屏以下由调用方传入，避免挤压正文。 */}
      {!inspectorInline ? inspector : null}
    </div>
  )
}
