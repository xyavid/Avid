/**
 * 三栏骨架（阶段 1 立，阶段 4 改插槽式，阶段 52 起两侧可拖）：只定宽高与栅格，
 * 对应报告 §6 的布局骨架：
 *
 *   顶栏 44px ｜ 侧栏 240px ｜ 对话列 ≤720px 居中（由表面自排）｜ 右栏 280px
 *
 * 两侧列的宽度是用户拖出来的偏好（`state/columns.ts` 负责持久化与夹取），初值取
 * 布局 token；对话列至少留 `MAIN_MIN_WIDTH`，窗口不够时先收右列、再收左列。
 * 拖动期间**直接改栅格模板、不走 React state**——主列里挂着整条对话，跟着每次
 * pointermove 重渲染太贵；松手才落一次盘。
 * 宽高全部引用 tokens.css 的布局 token；插槽缺省时显示阶段 1 的验收标注。
 * 表面（surfaces/*）负责往插槽里填内容与自己的居中滚动结构。
 */

import { useRef } from 'react'
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'

import type { ColumnId, ColumnWidths } from '../state/columns'
import { clampWidths, useColumns, useViewportWidth } from '../state/columns'
import { AvidMark } from '../ui/Mark'
import { cx } from '../ui/cx'

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
  const { widths, resize, reset } = useColumns()
  const viewport = useViewportWidth()
  const gridRef = useRef<HTMLDivElement>(null)
  const hasRail = rail !== undefined
  const shown = clampWidths(widths, viewport, hasRail)

  const template = (value: ColumnWidths) =>
    hasRail ? `${value.sidebar}px minmax(0,1fr) ${value.rail}px` : `${value.sidebar}px minmax(0,1fr)`

  /** 从指针位移算宽度：左列往右拖变宽，右列往左拖变宽。 */
  const beginDrag = (id: ColumnId) => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const start = shown[id]
    let wanted = start
    const move = (moveEvent: PointerEvent) => {
      const delta = id === 'sidebar' ? moveEvent.clientX - startX : startX - moveEvent.clientX
      wanted = start + delta
      // 拖动中只动模板：夹取照旧（不能把对话列挤没），但不惊动 React。
      if (gridRef.current !== null) {
        gridRef.current.style.gridTemplateColumns = template(
          clampWidths({ ...shown, [id]: wanted }, viewport, hasRail),
        )
      }
    }
    const finish = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      // 落盘记的是「想要多少」，不是这一帧夹过的值——窗口变宽时偏好还在。
      resize(id, wanted)
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
  }

  /** 键盘也能调：方向键 16px，按住 Shift 48px。 */
  const nudge = (id: ColumnId) => (event: KeyboardEvent<HTMLDivElement>) => {
    const grow = id === 'sidebar' ? 'ArrowRight' : 'ArrowLeft'
    const shrink = id === 'sidebar' ? 'ArrowLeft' : 'ArrowRight'
    const step = (event.shiftKey ? 48 : 16) * (event.key === grow ? 1 : event.key === shrink ? -1 : 0)
    if (step === 0) return
    event.preventDefault()
    resize(id, shown[id] + step)
  }

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
        ref={gridRef}
        className="grid min-h-0"
        style={{ gridTemplateColumns: template(shown) }}
      >
        <aside className="relative flex min-h-0 flex-col border-r border-hair bg-sidebar p-a12">
          {sidebar ?? <RegionNote>侧栏 240px · 会话列表占位</RegionNote>}
          <ColumnHandle
            side="right"
            label="调整侧栏宽度"
            onPointerDown={beginDrag('sidebar')}
            onKeyDown={nudge('sidebar')}
            onDoubleClick={() => reset('sidebar')}
          />
        </aside>

        <main className="min-h-0 overflow-hidden">{main ?? <RegionNote>对话列 ≤720px · 阶段 4 组装</RegionNote>}</main>

        {/* 右列只给栅格与滚动边界：边线、内边距、滚动条样式都由插槽内容自带
            （dock 的头部要贴着列缘）。 */}
        {rail && (
          <aside className="relative min-h-0 overflow-hidden">
            {rail}
            <ColumnHandle
              side="left"
              label="调整侧边栏宽度"
              onPointerDown={beginDrag('rail')}
              onKeyDown={nudge('rail')}
              onDoubleClick={() => reset('rail')}
            />
          </aside>
        )}
      </div>
    </div>
  )
}

/**
 * 列缘上的拖拽柄：压在发丝线上，悬停/聚焦时现形。
 * 键盘：方向键 16px、Shift 48px（左列 ArrowRight 变宽，右列 ArrowLeft 变宽）；
 * 双击复位到布局 token 的初值。
 */
function ColumnHandle({
  side,
  label,
  onPointerDown,
  onKeyDown,
  onDoubleClick,
}: {
  side: 'left' | 'right'
  label: string
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void
  onDoubleClick: () => void
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={onDoubleClick}
      className={cx(
        'absolute inset-y-0 z-10 w-[7px] cursor-col-resize transition-colors duration-fast ease-out hover:bg-accent/25 focus-visible:bg-accent/40 focus:outline-none',
        side === 'right' ? '-right-[4px]' : '-left-[4px]',
      )}
    />
  )
}
