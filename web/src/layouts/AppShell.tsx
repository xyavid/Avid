/**
 * 应用外壳：**只做布局**。
 *
 * 边界写清楚：外壳不知道会话、不知道图标、不知道任何领域概念——它只把三栏摆好，
 * 并保证高度链正确。收起按钮、图标、文案都由各自的栏负责（导航列自己带收起按钮）。
 * 这样"外壳"在换页面结构时是唯一不用跟着改的文件。
 *
 * 高度链是本文件最容易出错的地方，说明一次：
 *   `h-dvh`（根，竖向 flex）→ 顶部条 `shrink-0 h-[44px]` →
 *   三栏容器 `flex-1 min-h-0` → 各栏 `h-full min-h-0` →
 *   消息区 `flex-1 min-h-0 overflow-y-auto`。
 * 少了任何一个 `min-h-0`，flex 子项的最小高度会等于内容高度，消息区就会把整页撑高、
 * 把输入区推出视口之外（表现为"整页有了滚动条，输入框看不见了"）。
 * 顶部条进来之后，三栏容器从"根的直接子项"变成"顶部条下面的 flex-1 项"，所以
 * 那两条类名（`flex-1` 与 `min-h-0`）是新链上必须补的一环——**不要**把三栏容器
 * 改回 `h-full`：留给它的高度是"视口减 44px"，这是由 flex 算出来的，不是全高。
 * 这条链的类名契约由 `__tests__/AppShell.test.tsx` 钉住，真实视口行为由
 * `e2e/layout.spec.ts` 与 `e2e/smoke.spec.ts` 在 chromium 里量。
 *
 * 宽度值 `w-60` / `w-12` 对应 token `--avid-nav-w`(240px) 与
 * `--avid-nav-w-collapsed`(48px)，恰好落在 Tailwind 标尺上，所以不写任意值。
 * 检查器的 380px（`--avid-inspector-w`）不在标尺上，用任意值并在此注明来源。
 * 顶部条的 44px（`--avid-titlebar-h`）同样不在标尺上。
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
  /** 顶部条左端的内容（截图里是收起侧栏的图标按钮）。 */
  topbarLeft?: ReactNode
  /** 顶部条中间的内容（胶囊标签）。由页面决定放什么。 */
  topbarCenter?: ReactNode
  /** 顶部条右侧的内容（窗口控制等）。 */
  topbarRight?: ReactNode
}

export function AppShell({
  nav,
  children,
  inspector,
  navCollapsed,
  inspectorInline,
  navInline,
  topbarLeft,
  topbarCenter,
  topbarRight,
}: AppShellProps) {
  return (
    /*
     * 纸面肌理**挂在根元素自己的 background-image 上**，不另起一个
     * `absolute inset-0 -z-10` 的兄弟层。
     *
     * 原因（实测踩过）：父元素一旦有不透明背景色（这里是 `bg-canvas`），
     * 子元素的负 z-index 依然会被**父元素自己的背景**盖住——`-z-10` 只保证它在
     * 兄弟内容之下，不保证在父元素背景之下。表现是纸纹完全看不见，而 DOM 里那个
     * div 还在、`background-image` 也解析正常，光看结构查不出来。
     *
     * 挂在根上就是标准的多层背景：`background-image` 永远画在 `background-color`
     * 之上，颗粒感可见，且不额外产生一个节点、不参与 z-index 计算。
     */
    <div
      className="relative flex h-dvh w-full flex-col overflow-hidden bg-canvas"
      style={{ backgroundImage: 'var(--avid-paper-fiber)' }}
    >
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

      {/*
        顶部条：同样是**纯布局**——外壳不知道里面放的是什么，只提供三段插槽。
        三条决定：
          · `shrink-0` + 固定 44px（token `--avid-titlebar-h`）：它不参与 scroll，
            下面的三栏容器自己去吃剩下的高度；
          · 底色用左栏色 `bg-deep`：截图里顶部条与左栏是同一层，二者连成"壳"，
            主区纸面被框在中间；
          · 单边发丝线只写 `border-b-hair`（方向变体自带线色）。写
            `border-b-hair border-hair` 会把四边都设成 0.5px、画成一个整框——
            这是本仓踩过的坑，见 tailwind.config.js 里的注释。
        左右两段用 `flex-1` 而不是 `justify-between`：两段等宽时中段才真的居中，
        否则中段会随左右内容的宽度漂移。
      */}
      <header className="flex h-[44px] shrink-0 items-center gap-a8 border-b-hair bg-deep px-a12">
        <div className="flex min-w-0 flex-1 items-center gap-a4">{topbarLeft}</div>
        <div className="flex min-w-0 shrink-0 items-center justify-center">{topbarCenter}</div>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-a4">{topbarRight}</div>
      </header>

      <div className="flex min-h-0 min-w-0 w-full flex-1">
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
