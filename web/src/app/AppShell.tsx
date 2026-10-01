/**
 * 三栏空壳（阶段 1）——只定宽高与栅格，对应报告 §6 的布局骨架：
 *
 *   顶栏 44px ｜ 侧栏 240px ｜ 对话列 ≤720px 居中 ｜ 右栏 280px
 *
 * 宽高全部引用 tokens.css 的布局 token（经 Tailwind 绑定），不写裸像素。
 * 各区域里的灰字是**验收标注**（region 名 + token 尺寸），方便对着参考图
 * 核对分栏比例；它们不是 UI 文案，后续阶段会被真实内容替换。
 * 阶段纪律：本文件不引入组件、不配色块——结构之外只有纸面底色与发丝线。
 */

export function AppShell() {
  return (
    <div className="grid h-dvh grid-rows-[var(--titlebar-h)_minmax(0,1fr)] bg-paper font-ui text-ink">
      <header className="flex items-center justify-between border-b border-hair px-a16">
        <span className="font-serif text-title tracking-[0.01em]">Avid</span>
        {/* 主题切换与服务状态的预留位（阶段 6 / 接线阶段） */}
        <span className="font-mono text-micro text-ink-muted">主题 · 状态占位</span>
      </header>

      <div className="grid min-h-0 grid-cols-[var(--sidebar-width)_minmax(0,1fr)_var(--channel-inspector-width)]">
        <aside className="flex min-h-0 flex-col gap-a12 border-r border-hair bg-sidebar p-a12">
          {/* 搜索框占位：只留 30px 高的发丝线框（报告 §7.2 的高度），阶段 3 实做 */}
          <div className="h-[30px] rounded-sm border border-hair" aria-hidden />
          <span className="font-mono text-micro text-ink-muted">侧栏 240px · 会话列表占位</span>
        </aside>

        <main className="min-h-0 overflow-y-auto">
          <div className="mx-auto flex h-full max-w-chat flex-col items-center justify-center">
            <span className="font-mono text-micro text-ink-muted">对话列 ≤720px · 阶段 4 组装</span>
          </div>
        </main>

        <aside className="min-h-0 border-l border-hair p-a12">
          <span className="font-mono text-micro text-ink-muted">右栏 280px · 上下文占位</span>
        </aside>
      </div>
    </div>
  )
}
